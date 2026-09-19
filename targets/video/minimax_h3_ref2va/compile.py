"""
MiniMax H3 Ref2VA: the story IR compiled into the shotlist H3ShotListLoader
reads (shotlist/shotlist.json, shotlist_proxy.json).

Everything H3-specific about a build is here or in target.json beside it: the
17k+5 frame grid and the /32 size rule (template), reference slots, panels and
the plate as Picture 4 (recipe), the 3-voice clone limit, audio policy and
retention, the turbo-LoRA presets and their step-count check, and the Ref2VA
prompt (prompt.py). The core hands over the parsed story (h3core.ir) and the
series config; seeds still come from h3core.ir.stable_seed.

    compile_episode(target, story, series_cfg, pass_) -> (doc, report)
    compile_shot(target, shot_ir, series_cfg, preset, ctx) -> one entry
    compile_without(target, story, series_cfg, pass_, entry, missing) -> entry
    required_refs(target, shot_ir, series_cfg, ctx) -> [RefRequest]
    ref_slots(target, doc, shot) -> [{slot, kind, path, subject?}]

The compile works on the parser's dict shape (legacy_episode rebuilds it from
the IR), which is what the prompt writer was written against.

Stdlib only.
"""
from __future__ import annotations

import re

import targets as TG
from h3core import ir
from h3core.ir import stable_seed
from h3core.speech import RATE_CEILING, SPEECH_RATE, forced_rate, pacing, speech_seconds

from .prompt import build_prompt

ID = "minimax_h3_ref2va"
TARGET = TG.load_target(ID, "video")
RECIPE = TARGET.recipe

# How a recorded-dialogue (dub) shot treats its audio slice. H3's retention
# markers: fully_copy makes the slice the shot's ENTIRE audio track (lips follow
# it, no foley is added); partially_copy copies the voice and lets H3 add the
# ambience and action sounds around it; reference only borrows timbre and
# timing (the old behaviour -- mouths drift into gibberish). clone and generate
# shots have no recording to copy, so they are always `reference` / none.
RETENTIONS = tuple(RECIPE["retention"]["values"])
RETENTION_DEFAULT = dict(RECIPE["retention"]["default"])
# The IR's neutral `preserve` -> H3's retention marker.
RETENTION_OF = dict(RECIPE["retention"]["from_preserve"])
SUBJECT_SLOTS = list(RECIPE["subject_slots"])
PLATE_SLOT = RECIPE["plate_slot"]
VOICE_SLOTS = list(RECIPE["voice_slots"])
GENERATE_MODES = ("generate", "generated", "generated_audio")


# ---------------------------------------------------------------------------
# Story IR -> the episode dict the compile code reads
#
# This is the parser's original dict shape (h3core.story.parse_script), rebuilt
# from the IR so that compile only ever sees what the IR carries. Values the
# parser could not interpret (`unparsed`) go back in verbatim, so compile
# rejects them with the same message it always has.
# ---------------------------------------------------------------------------

def _put_overrides(d: dict, o: dict, unparsed: dict) -> None:
    for k in ("model", "lora"):
        if o.get(k):
            d[k] = o[k]
    if o.get("steps") is not None:
        d["steps"] = o["steps"]
    elif "steps" in unparsed:
        d["steps"] = unparsed["steps"]


def _put_choice(d: dict, node) -> None:
    """`profile:` and `target:`, when the script sets them."""
    for k in ("profile", "target"):
        if getattr(node, k, None):
            d[k] = getattr(node, k)


def legacy_sequence(sq: ir.Sequence) -> dict:
    seq = {"id": sq.id, "location_key": sq.location, "continuous": sq.continuous,
           "shots": []}
    _put_overrides(seq, sq.overrides, sq.unparsed)
    _put_choice(seq, sq)
    return seq


def legacy_shot(s: ir.Shot) -> dict:
    sh = {"id": s.id, "cast": list(s.cast), "props": list(s.props), "size": s.size,
          "dialogue": [{"who": d.speaker, "mode": "" if d.mode == "on" else d.mode,
                        "delivery": d.delivery, "line": d.line}
                       for d in s.dialogue],
          "action": s.action, "plate": s.plate}
    t = s.timing or {}
    if "audio_in" in t:
        sh["audio_in"], sh["audio_out"] = t["audio_in"], t["audio_out"]
    elif t.get("auto"):
        sh["duration_auto"] = True
    elif "seconds" in t:
        sh["duration"] = t["seconds"]
    if s.pace:
        sh["pace"] = s.pace
    for k in ("camera", "sound", "music", "extras", "text"):
        if getattr(s, k):
            sh[k] = getattr(s, k)
    if s.audio:
        sh["policy"] = s.audio
    if s.preserve:
        sh["retention"] = RETENTION_OF[s.preserve]
    elif "preserve" in s.unparsed:
        sh["retention"] = s.unparsed["preserve"]
    _put_overrides(sh, s.overrides, s.unparsed)
    _put_choice(sh, s)
    return sh


def legacy_episode(story: ir.Episode) -> dict:
    ep = {"id": story.id, "title": story.title, "sequences": []}
    for sq in story.sequences:
        seq = legacy_sequence(sq)
        seq["shots"] = [legacy_shot(s) for s in sq.shots]
        ep["sequences"].append(seq)
    return ep


# ---------------------------------------------------------------------------
# One episode's compile state
# ---------------------------------------------------------------------------

class Ctx:
    """What every shot of one pass shares: the preset, the audio policy, and
    what the compile collects (warnings, needed refs, blocked shots, totals).

    `absent` names references to compile WITHOUT (render anyway, see
    compile_shot): subject ids and/or "plate"."""

    def __init__(self, target, ep_id: str, series_cfg: dict, pass_: str,
                 absent: set[str] | None = None):
        self.target, self.ep_id, self.series_cfg, self.pass_ = target, ep_id, series_cfg, pass_
        self.proxy = pass_ == "proxy"
        s = series_cfg["series"]
        self.fps = float(s.get("fps", 24))
        self.preset = target.preset(pass_, series_cfg)
        target.template.validate_size(self.preset.width, self.preset.height)
        self.book = series_cfg["subjects"]
        audio_cfg = series_cfg.get("audio", {})
        mode = audio_cfg.get("mode", "source_track")
        track = audio_cfg.get("track", "")
        # The recorded dialogue still times the picture when the proxy lets H3
        # invent the voices, and assemble lays it under the cut to check sync.
        self.recording = track

        # The proxy may run a different audio mode. The animatic exists to judge
        # staging, framing and pacing, and waiting on voice samples to see any of
        # that is a bad trade — so `proxy.audio_mode: generate` lets H3 invent the
        # voices for the cheap pass while the final still clones from your samples.
        # Seeds, lengths and subjects are unaffected, so the passes stay comparable.
        if self.proxy:
            pm = series_cfg.get("proxy", {}).get("audio_mode")
            if pm:
                mode = pm
                if pm in GENERATE_MODES:
                    track = ""
        if mode == "source_track":
            speaking_policy = "dub_keep_foley"
        elif mode in GENERATE_MODES:
            # No reference of any kind: H3 speaks the written lines in a voice of
            # its own choosing, guided only by each character's `voice` line in the
            # prompt. Needs no recordings and no voice samples. Expect the voice to
            # drift between shots — fine for a pilot, not for a long series.
            speaking_policy = "generate"
        else:
            speaking_policy = "clone"
        self.speaking_policy = series_cfg.get("audio", {}).get("default_policy",
                                                               speaking_policy)
        self.warnings: list[str] = []
        self.needed: dict[str, dict] = {}
        self.blocked: dict[str, list[str]] = {}
        self.total_req = self.total_raw = 0
        self.image = TG.image_target(series_cfg)
        self.profiles = TG.series_profiles(series_cfg)
        self.absent = set(absent or ())

    # -- refs --------------------------------------------------------------

    def need(self, path, kind, prompt, shot_id=None):
        if path:
            self.needed.setdefault(path, {"kind": kind, "prompt": prompt})
            if shot_id and shot_id not in self.blocked.setdefault(path, []):
                self.blocked[path].append(shot_id)

    def wording(self, req: TG.RefRequest) -> str:
        """A ref request's prompt: the image target words pictures; a voice
        sample is a note for whoever records it."""
        if req.shape == "voice":
            e = req.entry
            return TG.voice_prompt(e['name'], e.get('voice', 'as written in series.json'))
        return self.image.ref_prompt(req, self.series_cfg)

    def resolve_plate(self, key: str, where: str, shot_id: str | None = None) -> dict:
        """A location entry, validated, registered as a needed asset."""
        series_cfg = self.series_cfg
        if key not in series_cfg["locations"]:
            raise ValueError(
                f"{where}: location '{key}' is not in series.json "
                f"({', '.join(series_cfg['locations'])})")
        entry = series_cfg["locations"][key]
        if not entry.get("plate"):
            raise ValueError(
                f"location '{key}': needs a `plate` path. The background "
                f"plate is <{PLATE_SLOT}> in every shot there.")
        req = plate_request(key, entry)
        self.need(req.path, req.kind, self.wording(req), shot_id)
        return entry

    def register_sequence(self, seq: dict) -> dict:
        """Validate and register the sequence's plate, then each shot's own
        angle (`plate:`), before any shot compiles. Returns the sequence's
        location entry."""
        seq_loc = self.resolve_plate(seq["location_key"], f"sequence {seq['id']}")
        # A sequence is a scene; a shot may name its own angle on that scene
        # with `plate:`. Cutting between angles of one room is the normal case,
        # so this lives on the shot rather than forcing a sequence break —
        # which would also re-seed every shot in it.
        for _sh in seq["shots"]:
            _loc = (self.resolve_plate(_sh["plate"], f"shot {_sh['id']}", _sh["id"])
                    if _sh.get("plate") else seq_loc)
            if _sh["id"] not in self.blocked.setdefault(_loc["plate"], []):
                self.blocked[_loc["plate"]].append(_sh["id"])
        return seq_loc

    def policy(self, shot: dict) -> str:
        return shot.get("policy") or (self.speaking_policy if shot["dialogue"] else "generate")


# ---------------------------------------------------------------------------
# ref requests: the SHAPE of each ref this target reads (the wording is the
# image target's)
# ---------------------------------------------------------------------------

def _refs(key: str) -> dict:
    return RECIPE["refs"][key]


def _hint(kind: str) -> str:
    return RECIPE["size_hints"].get(kind, "")


def plate_request(key: str, entry: dict) -> TG.RefRequest:
    r = _refs("location")
    return TG.RefRequest(entry.get("plate") or "", r["kind"], r["shape"], location=key,
                         slot=PLATE_SLOT, entry=entry, size_hint=_hint(r["kind"]))


def subject_request(sid: str, entry: dict, slot: str | None = None) -> TG.RefRequest:
    kind = entry.get("kind", "character")
    if kind == "character":
        r = _refs("character")
        label, views = r["kind"], int(r.get("views", 1))
    else:
        r = _refs("object")
        label, views = r["kind"].format(kind=entry.get("kind", "prop")), 1
    return TG.RefRequest(entry.get("sheet") or "", label, r["shape"], subject=sid,
                         views=views, slot=slot, entry=entry, size_hint=_hint(label))


def voice_request(sid: str, entry: dict, slot: str | None = None) -> TG.RefRequest:
    r = _refs("voice")
    return TG.RefRequest(entry.get("voice_sample") or "", r["kind"], r["shape"], subject=sid,
                         slot=slot, entry=entry, size_hint=_hint(r["kind"]))


# ---------------------------------------------------------------------------
# Compile
# ---------------------------------------------------------------------------

def _subjects(shot: dict) -> list[str]:
    # Characters first, then props/vehicles. Slots 1-3 only; slot 4 is
    # always the location plate.
    return shot["cast"] + [p for p in shot["props"] if p not in shot["cast"]]


def _voices(shot: dict) -> list[str]:
    # Speakers can be off screen, so the voice sample a `clone` shot
    # needs is not necessarily one of the visible subjects.
    voices: list[str] = []
    for d in shot["dialogue"]:
        if d["who"] not in voices:
            voices.append(d["who"])
    return voices


def _compile_shot(ctx: Ctx, seq: dict, i: int, shot: dict, seq_loc: dict) -> dict:
    series_cfg, book, fps = ctx.series_cfg, ctx.book, ctx.fps
    template = ctx.target.template
    warnings, need = ctx.warnings, ctx.need
    loc = (series_cfg["locations"][shot["plate"]] if shot.get("plate") else seq_loc)
    subjects = _subjects(shot)
    # A shot may legitimately have no visible subjects: an establishing
    # plate with voiceover over it. The location plate then fills all
    # four slots and becomes <Subject 1>. Only an empty shot is an error.
    if not subjects and not shot["dialogue"] and not shot["action"]:
        raise ValueError(
            f"shot {shot['id']}: nothing in it. Add `who:`, `with:`, action "
            f"text, or dialogue.")
    n_slots = len(SUBJECT_SLOTS)
    if len(subjects) > n_slots:
        over = len(subjects) - n_slots
        raise ValueError(
            f"shot {shot['id']}: {len(subjects)} subjects ({', '.join(subjects)}) but "
            f"only {n_slots} slots — slot {n_slots + 1} is the background. Drop {over} from "
            f"`with:`, or describe {over} in the action text instead of referencing "
            f"{'them' if over > 1 else 'it'}.")
    if ctx.absent:
        # render anyway: a subject whose picture is missing is written in
        # words instead of taking a slot
        shot["_unreferenced"] = [s for s in subjects if s in ctx.absent]
        subjects = [s for s in subjects if s not in ctx.absent]
        shot["_no_plate"] = "plate" in ctx.absent
    shot["_subjects"] = subjects

    # duration
    pace = shot.get("pace") or series_cfg.get("speech", {}).get("pace", "normal")
    if pace not in SPEECH_RATE:
        raise ValueError(
            f"series.json speech.pace '{pace}' must be one of {sorted(SPEECH_RATE)}")
    need_sec = speech_seconds(shot["dialogue"], pace)

    if "audio_in" in shot:
        dur = shot["audio_out"] - shot["audio_in"]
        if not ctx.recording:
            raise ValueError(
                f"shot {shot['id']}: uses an `audio:` window but series.json has no "
                f"audio.track set.")
    elif shot.get("duration_auto"):
        if not shot["dialogue"]:
            raise ValueError(
                f"shot {shot['id']}: `dur: auto` needs dialogue to measure. "
                f"Give a silent shot an explicit `dur:`.")
        dur = need_sec
    elif "duration" in shot:
        dur = shot["duration"]
    else:
        raise ValueError(
            f"shot {shot['id']}: needs `audio: 3.10-7.40`, `dur: 3.04`, or `dur: auto`")
    req = max(1, round(dur * fps))
    raw = template.snap(req)

    # Does the dialogue actually fit the window H3 will render?
    if shot["dialogue"]:
        held = raw / fps
        rate = forced_rate(shot["dialogue"], held)
        syl, gaps = pacing(shot["dialogue"])
        fits = template.snap(max(1, round(need_sec * fps))) / fps
        # what this window actually holds at a followable rate
        holds = max(0, int(SPEECH_RATE[pace] * max(0.0, held - gaps)))
        if rate > RATE_CEILING:
            warnings.append(
                f"{shot['id']}: CRAMMED — {syl} syllables in {held:.2f}s forces "
                f"{rate:.1f} syl/s (ceiling {RATE_CEILING}). This window holds about "
                f"{holds}. Either `dur: {fits:.2f}` (+{fits - held:.2f}s), cut "
                f"~{max(1, syl - holds)} syllables, or split the shot.")
        elif rate > SPEECH_RATE[pace] + 0.15:
            fix = (f"`dur: {fits:.2f}` gives it room"
                   if fits > held else "trim a few syllables")
            alt = ("" if pace == "fast"
                   else ", or add `pace: fast` if the rush is the joke")
            warnings.append(
                f"{shot['id']}: tight — {syl} syllables in {held:.2f}s forces "
                f"{rate:.1f} syl/s against a {pace} pace of {SPEECH_RATE[pace]}. "
                f"{fix}{alt}.")
    ctx.total_req += req
    ctx.total_raw += raw

    policy = ctx.policy(shot)
    voices = _voices(shot)
    for v in voices:
        if book[v].get("kind", "character") != "character":
            raise ValueError(
                f"shot {shot['id']}: '{v}' speaks but is not kind=character.")
    shot["_policy"] = policy
    shot["_audio_ref"] = policy != "generate"
    ret_req = (shot.get("retention") or "").strip().lower()
    if ret_req and ret_req not in RETENTIONS:
        raise ValueError(f"shot {shot['id']}: retention '{ret_req}' must be one of "
                         f"{', '.join(RETENTIONS)}")
    if policy in RETENTION_DEFAULT:
        retention = (ret_req or series_cfg.get("audio", {}).get("retention", "").lower()
                     or RETENTION_DEFAULT[policy])
        if retention not in RETENTIONS:
            raise ValueError(f"series.json audio.retention '{retention}' must be one of "
                             f"{', '.join(RETENTIONS)}")
        if "audio_in" not in shot:
            raise ValueError(
                f"shot {shot['id']}: policy {policy} needs an `audio: in-out` window "
                f"on the recorded track (h3align.py writes these).")
        if retention == "fully_copy" and shot.get("sound") and policy == "dub_keep_foley":
            warnings.append(
                f"{shot['id']}: fully_copy makes the recording the whole soundtrack, so "
                f"H3 adds no foley and dub_keep_foley's foley bed will be near-empty. "
                f"Use `retention: partially_copy` to keep foley.")
    else:
        if ret_req and ret_req != "reference":
            raise ValueError(
                f"shot {shot['id']}: retention {ret_req} needs recorded dialogue "
                f"(policy dub or dub_keep_foley); a {policy} shot has nothing to copy.")
        retention = "reference" if shot["_audio_ref"] else ""
    if "audio" in ctx.absent and policy != "generate":
        # render anyway with a voice sample or the recording missing: no audio
        # reference at all, the written lines voiced from each `voice` line
        policy, retention = "generate", ""
        shot["_policy"], shot["_audio_ref"] = policy, False
    shot["_retention"] = retention
    shot["_continuation"] = seq["continuous"] and i > 0

    # Always one panel. A 2- or 4-panel strip is a multi-figure image,
    # and H3 renders it as multiple people -- worst on wides. The face
    # panel carries identity where it matters; the three-quarter body
    # panel carries wardrobe and proportion everywhere else.
    panels = int(RECIPE["panels"])
    panel_view = ("face" if (len(subjects) == 1
                  and book[subjects[0]].get("kind", "character") == "character"
                  and shot["size"] in RECIPE["face_sizes"]) else "body")

    for s in subjects:
        r = subject_request(s, book[s])
        need(r.path, r.kind, ctx.wording(r), shot["id"])

    if seq["continuous"] and i > 0:
        w = template.continuous_warning(shot["id"], raw)
        if w:
            warnings.append(w)
    pad = raw - req
    if ("audio_in" not in shot and not shot.get("duration_auto")
            and pad / raw > 0.15):
        warnings.append(
            f"{shot['id']}: dur {dur:.2f}s snaps up to {raw / fps:.2f}s, wasting "
            f"{pad} frames. Writing it as {raw / fps:.2f}s costs the same render.")

    # H3 accepts at most 3 standalone audio references, and clone mode
    # spends one per speaker.
    if policy == "clone" and len(voices) > len(VOICE_SLOTS):
        raise ValueError(
            f"shot {shot['id']}: {len(voices)} speakers ({', '.join(voices)}) but "
            f"clone mode feeds one voice sample each and {ctx.target.short} takes at most "
            f"{len(VOICE_SLOTS)}. Split the shot, or move a line to a neighbouring shot.")

    voice_refs = []
    if policy == "clone":
        for v in voices:
            e = book[v]
            r = voice_request(v, e)
            need(r.path, r.kind, ctx.wording(r), shot["id"])
            voice_refs.append({"subject": v, "sample": e.get("voice_sample", "")})

    layers = TG.render_layers(series_cfg, seq, shot, ctx.profiles)
    entry = {
        "id": shot["id"],
        "sequence": seq["id"],
        "subjects": subjects,
        "background": "" if shot.get("_no_plate") else loc["plate"],
        "size": shot["size"],
        "panels": panels,
        "panel_view": panel_view,
        "length": raw,
        "seed": stable_seed(ctx.ep_id, seq["id"], shot["id"]),
        # the shot beats its profile beats the sequence beats its profile
        # beats the pass
        "steps": int(TG.layered(layers, "steps", ctx.preset.steps, present=True)),
        "audio_policy": policy,
        "audio_retention": retention,
        "voices": voices,
        "voice_subject": voices[0] if voices else "",
        "voice_refs": voice_refs,
        "prompt": build_prompt(shot, seq, series_cfg, panels, panel_view),
    }
    model = TG.layered(layers, "model")
    if model:
        entry["model"] = model
    lora = TG.layered_lora(layers)
    if lora:
        entry[lora[0]] = lora[1]
    profile = shot.get("profile") or seq.get("profile")
    if profile:
        entry["profile"] = profile
    if "audio_in" in shot:
        entry["audio_in"], entry["audio_out"] = shot["audio_in"], shot["audio_out"]
    else:
        entry["duration"] = round(dur, 3)
    return entry


def _lora_list(loras: list[dict]) -> str:
    return ", ".join(f"{lo['name']}@{lo.get('strength', 1.0):g}" for lo in loras) or "none"


def compile_legacy(target, ep: dict, series_cfg: dict, pass_: str,
                   only: set[str] | None = None) -> tuple[dict, dict]:
    """The parser-shaped episode -> (shotlist doc, report). With `only`, just
    those shots (the rest of an episode that mixes targets is another
    target's); each keeps its place in its sequence, so it compiles exactly as
    in a full build."""
    ctx = Ctx(target, ep["id"], series_cfg, pass_)
    preset = ctx.preset
    width, height, steps, model, lora = (preset.width, preset.height, preset.steps,
                                         preset.model, preset.lora)
    shots_out = []
    for seq in ep["sequences"]:
        if only is not None:
            mine = [sh for sh in seq["shots"] if sh["id"] in only]
            if not mine:
                continue
            seq_loc = ctx.register_sequence(dict(seq, shots=mine))
        else:
            seq_loc = ctx.register_sequence(seq)
        for i, shot in enumerate(seq["shots"]):
            if only is not None and shot["id"] not in only:
                continue
            shots_out.append(_compile_shot(ctx, seq, i, shot, seq_loc))

    book, warnings = ctx.book, ctx.warnings
    doc = {
        # h3assemble names the review cut from these
        "episode": ep["id"],
        "title": ep.get("title", ""),
        "prompt_prefix": "",
        "defaults": {
            "width": width, "height": height, "steps": steps,
            "model": model, "lora": lora, "sheet_panels": int(RECIPE["sheet_panels"]),
            "audio_policy": "generate", "master_track": ctx.recording,
        },
        "subjects": {
            s: {"kind": e.get("kind", "character"),
                "sheet": e.get("sheet", ""),
                "voice_sample": e.get("voice_sample", "")}
            for s, e in book.items()
        },
        "shots": shots_out,
    }

    for _o in sorted({f"{sh['id']} overrides {k}: {sh[k]}"
                      for sh in shots_out for k in ("model", "lora") if k in sh}
                     | {f"{sh['id']} overrides loras: {_lora_list(sh['loras'])}"
                        for sh in shots_out if "loras" in sh}
                     | {f"{sh['id']} overrides steps: {sh['steps']}"
                        for sh in shots_out if sh["steps"] != steps}):
        warnings.append(_o)

    # A distilled turbo LoRA is trained for one step count.
    m = re.search(r"(\d+)step", lora or "")
    if m and int(m.group(1)) != steps:
        warnings.append(f"{'proxy' if ctx.proxy else 'series'}.steps is {steps} but the LoRA "
                        f"{lora} is distilled for {m.group(1)} steps — set steps: {m.group(1)} "
                        f"or pick a matching lora")

    fps = ctx.fps
    report = {
        "episode": ep["id"], "title": ep["title"],
        "mode": "proxy" if ctx.proxy else "final",
        "resolution": f"{width}x{height}", "steps": steps,
        "model": model, "lora": lora, "fps": fps,
        "shots": len(shots_out),
        "sequences": (len(ep["sequences"]) if only is None else
                      len({sh["sequence"] for sh in shots_out})),
        "target_s": ctx.total_req / fps, "delivered_s": ctx.total_raw / fps,
        "pad_frames": ctx.total_raw - ctx.total_req,
        "policies": {p: sum(1 for s in shots_out if s["audio_policy"] == p)
                     for p in sorted({s["audio_policy"] for s in shots_out})},
        "needed": ctx.needed, "blocked_shots": ctx.blocked, "warnings": warnings,
        "target": target.id, "size_hints": dict(RECIPE["size_hints"]),
    }
    return doc, report


def compile_episode(target, story: ir.Episode, series_cfg: dict,
                    pass_: str, only: set[str] | None = None) -> tuple[dict, dict]:
    return compile_legacy(target, legacy_episode(story), series_cfg, pass_, only)


def _find(story: ir.Episode, shot_id: str) -> tuple[ir.Sequence, int]:
    for sq in story.sequences:
        for i, s in enumerate(sq.shots):
            if s.id == shot_id:
                return sq, i
    raise KeyError(f"{shot_id} is not in episode {story.id}")


def compile_shot(target, shot_ir: ir.Shot, series_cfg: dict, preset, ctx=None) -> dict:
    """One shot's shotlist entry, as compile_episode would build it.

    `preset` is the pass ("final" / "proxy"; a Preset's name is used).
    `ctx` is {"episode": ir.Episode (required: the shot's sequence and place
    in it matter), "absent": [subject ids and/or "plate"]}. Warnings and refs
    are collected on a fresh context and dropped."""
    ctx = dict(ctx or {})
    story = ctx.get("episode")
    if story is None:
        raise ValueError("compile_shot needs ctx['episode'], the shot's ir.Episode")
    pass_ = preset if isinstance(preset, str) else preset.name
    sq, i = _find(story, shot_ir.id)
    c = Ctx(target, story.id, series_cfg, pass_, ctx.get("absent"))
    seq = legacy_sequence(sq)
    seq["shots"] = [legacy_shot(s) for s in sq.shots]
    seq_loc = c.register_sequence(seq)
    return _compile_shot(c, seq, i, seq["shots"][i], seq_loc)


def compile_without(target, story: ir.Episode, series_cfg: dict, pass_: str,
                    entry: dict, missing: list[dict]) -> dict:
    """`entry` (a built shot) compiled again as if the `missing` refs (h3jobs
    ref_slots dicts) didn't exist, for rendering anyway: a missing subject
    drops out of the Picture slots and is described in words, a missing plate
    leaves <Picture 4> unmentioned (the loader still feeds grey there), and a
    missing voice sample or recording makes the shot `generate`.

    Raises ValueError when the story or series config no longer builds this
    exact entry (the build is out of date), so the caller can fall back to
    grey stand-ins rather than render a shot the build doesn't describe."""
    shot_ir = next((s for s in story.shots() if s.id == entry["id"]), None)
    if shot_ir is None:
        raise ValueError(f"{entry['id']} is not in shots.json")
    if compile_shot(target, shot_ir, series_cfg, pass_, {"episode": story}) != entry:
        raise ValueError(f"{entry['id']}: the build is out of date (rebuild the episode)")
    by_slot = dict(zip(SUBJECT_SLOTS, entry.get("subjects") or []))
    absent = set()
    for r in missing:
        if r.get("kind") == "audio":
            absent.add("audio")
        elif r.get("slot") == PLATE_SLOT:
            absent.add("plate")
        elif r.get("slot") in by_slot:
            absent.add(by_slot[r["slot"]])
    return compile_shot(target, shot_ir, series_cfg, pass_,
                        {"episode": story, "absent": absent})


def required_refs(target, shot_ir: ir.Shot, series_cfg: dict, ctx=None) -> list[TG.RefRequest]:
    """The refs a shot reads, in slot order: subjects, the plate, then voice
    samples when the shot clones. `ctx` may give {"pass": "proxy"} (the proxy
    can run another audio mode) and {"sequence": ir.Sequence}."""
    ctx = dict(ctx or {})
    c = Ctx(target, "", series_cfg, ctx.get("pass", "final"))
    shot = legacy_shot(shot_ir)
    book = series_cfg["subjects"]
    out = [subject_request(s, book[s], slot)
           for s, slot in zip(_subjects(shot), SUBJECT_SLOTS)]
    key = shot_ir.plate or (ctx["sequence"].location if ctx.get("sequence") else "")
    if key in series_cfg["locations"]:
        out.append(plate_request(key, series_cfg["locations"][key]))
    if c.policy(shot) == "clone":
        out += [voice_request(v, book[v], slot)
                for v, slot in zip(_voices(shot), VOICE_SLOTS)]
    return out


def ref_slots(target, doc: dict, shot: dict) -> list[dict]:
    """Every reference the loader will read for `shot`: {slot, kind ("image" |
    "audio"), path, subject?}. Paths as the shotlist writes them (relative to
    the episode unless absolute); an empty path means the series config names none."""
    book = doc.get("subjects", {})
    out = []
    for slot, sid in zip(SUBJECT_SLOTS, shot.get("subjects") or []):
        out.append({"slot": slot, "kind": "image", "subject": sid,
                    "path": book.get(sid, {}).get("sheet", "")})
    out.append({"slot": PLATE_SLOT, "kind": "image", "path": shot.get("background", "")})
    policy = shot.get("audio_policy", "")
    if policy == "clone":
        for slot, r in zip(VOICE_SLOTS, shot.get("voice_refs") or []):
            out.append({"slot": slot, "kind": "audio",
                        "subject": r.get("subject", ""), "path": r.get("sample", "")})
    elif policy in ("dub", "dub_keep_foley"):
        out.append({"slot": VOICE_SLOTS[0], "kind": "audio", "path": shot.get("audio_file")
                    or doc.get("defaults", {}).get("master_track", "")})
    return out
