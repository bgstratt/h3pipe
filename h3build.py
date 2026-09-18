#!/usr/bin/env python3
"""
h3build.py — script.md + series.json  ->  shots.json (story IR) + shotlist.json

One command. You write a screenplay-flavoured script and a series bible;
this produces the shotlist the ComfyUI loader reads, a list of any reference
assets you still need to make, and a timing report.

    python3 h3build.py series.json script.md -o <project_root>
    python3 h3build.py series.json script.md -o <project_root> --proxy
    python3 h3build.py series.json script.md --check

It runs in three steps: parse the script into the model-free story IR
(h3core.story, written to shotlist/shots.json), then compile that IR for H3.
Everything H3-specific is the compile step, here: the 17k+5 frame grid, the
Ref2VA six-section prompt format, reference slot allocation, per-shot audio
policy and retention, and deterministic seeds.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys

# Sibling modules: ComfyUI's embedded Python (a ._pth install) doesn't put a
# script's own folder on sys.path, so do it here.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from h3core import ir
from h3core.bible import character_ids, load_bible, series_info, subject_ids
# Model-neutral pieces, re-exported under their old names: h3align and others
# import them from here.
from h3core.ir import stable_seed  # noqa: F401
from h3core.speech import (GAP_SAME, GAP_SPEAKER, HEAD_AIR, RATE_CEILING,  # noqa: F401
                           SPEECH_RATE, TAIL_AIR, forced_rate, pacing,
                           speech_seconds, syllables)
from h3core.story import (META_KEYS, OS_TOKENS, SIZES, VO_TOKENS,  # noqa: F401
                          ScriptError, parse_script, parse_story,
                          split_parenthetical)

GRID_STEP, GRID_BASE, GRID_MAX = 17, 5, 3592

# How a recorded-dialogue (dub) shot treats its audio slice. H3's retention
# markers: fully_copy makes the slice the shot's ENTIRE audio track (lips follow
# it, no foley is added); partially_copy copies the voice and lets H3 add the
# ambience and action sounds around it; reference only borrows timbre and
# timing (the old behaviour -- mouths drift into gibberish). clone and generate
# shots have no recording to copy, so they are always `reference` / none.
RETENTIONS = ("fully_copy", "partially_copy", "reference")
RETENTION_DEFAULT = {"dub": "fully_copy", "dub_keep_foley": "partially_copy"}
# The IR's neutral `preserve` -> H3's retention marker.
RETENTION_OF = {"strict": "fully_copy", "loose": "partially_copy", "style": "reference"}


# ---------------------------------------------------------------------------

def snap_up(frames: int) -> int:
    if frames <= GRID_BASE:
        return GRID_BASE
    v = GRID_STEP * math.ceil((frames - GRID_BASE) / GRID_STEP) + GRID_BASE
    if v > GRID_MAX:
        raise ValueError(
            f"{frames} frames is longer than H3's maximum of {GRID_MAX} "
            f"({GRID_MAX / 24:.2f}s). Split this shot into two.")
    return v


# ---------------------------------------------------------------------------
# Story IR -> the episode dict the H3 compile code reads
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


def legacy_episode(story: ir.Episode) -> dict:
    ep = {"id": story.id, "title": story.title, "sequences": []}
    for sq in story.sequences:
        seq = {"id": sq.id, "location_key": sq.location, "continuous": sq.continuous,
               "shots": []}
        _put_overrides(seq, sq.overrides, sq.unparsed)
        for s in sq.shots:
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
            seq["shots"].append(sh)
        ep["sequences"].append(seq)
    return ep


# ---------------------------------------------------------------------------
# Ref2VA prompt builder  (h3-prompt-writing six-section format)
# ---------------------------------------------------------------------------

def build_prompt(shot: dict, seq: dict, bible: dict, panels: int,
                 panel_view: str = "body") -> list[str]:
    """Ref2VA six-section prompt.

    Slots 1-3 carry the shot's subjects -- characters first, then props and
    vehicles, in script order. Slot 4 always carries the location plate. The
    background is written as a proper <Subject> sourced from <Picture 4>,
    because H3's spec lists scenes and environments as Subject content; a bare
    <Picture N> means "use this exact frame as a keyframe", which is not what a
    location plate is for.
    """
    book = bible["subjects"]
    look = bible["style"]["look"]
    loc = bible["locations"][shot.get("plate") or seq["location_key"]]
    env = loc["description"]

    subjects = shot["_subjects"]                      # ordered, <= 3
    subj = {s: f"<Subject {i + 1}>" for i, s in enumerate(subjects)}
    pic = {s: f"<Picture {i + 1}>" for i, s in enumerate(subjects)}
    bg_subj = f"<Subject {len(subjects) + 1}>"

    # Speaker IDs are assigned by order of actual vocal events and cover
    # off-screen voices too -- they speak, so they need an (Sx).
    spoken: list[str] = []
    for d in shot["dialogue"]:
        if d["who"] not in spoken:
            spoken.append(d["who"])
    spk = {c: f"(S{i + 1})" for i, c in enumerate(spoken)}

    introduced: set[str] = set()

    def speaker_label(who: str) -> str:
        """<Subject N> for anyone on screen; a voice description otherwise.

        H3's spec asks that a speaker's vocal identity — timbre, pitch, rate —
        be established the first time they speak. That matters most when no
        audio reference is fed at all: without it the model picks a voice with
        nothing to go on, and picks a different one next shot. So the bible's
        `voice` line rides along on each speaker's FIRST line in a shot and is
        dropped after, which keeps later lines from getting noisy.

        A speaker with no visual reference is identified by a voice description
        followed by (Sx), never by an invented Subject.
        """
        first = who not in introduced
        introduced.add(who)
        voice = (book[who].get("voice") or "").strip()
        if who in subj:
            if first and voice:
                return f"{subj[who]}, with a voice that is {voice}, {spk[who]}"
            return f"{subj[who]} {spk[who]}"
        if first and voice:
            return f"An off-screen voice that is {voice} {spk[who]}"
        return f"The same off-screen voice {spk[who]}"

    # A reference image containing more than one figure gets drawn as more than
    # one person. One panel = one figure = one character on screen.
    #
    # `extras:` relaxes only the second half of that. A shot whose action puts
    # other people in frame — a dance partner, a crowd — while the prompt still
    # insists exactly one person appears is a contradiction, and H3 resolves it
    # by duplicating the referenced character. Naming the extras as unnamed
    # figures who must not resemble the subject is what stops that.
    extras = (shot.get("extras") or "").strip()
    if panels == 1:
        vname = ("a head-and-shoulders facial close-up" if panel_view == "face"
                 else "a three-quarter view of the whole body")
        ref_clause = (f"a single reference image of ONE character, {vname}. "
                      f"Exactly one person appears in that image.")
        if extras:
            ref_clause += (f" This character appears exactly once in the shot. The other "
                           f"people in frame are {extras}: unnamed background figures who "
                           f"must look nothing like this character and are never duplicates "
                           f"of them.")
        else:
            ref_clause += " Exactly one person appears in this shot."
    else:
        views = ("four views (three-quarter body, side profile, back view, and a facial "
                 "close-up)" if panels == 4 else
                 "two views (three-quarter body and a facial close-up)")
        ref_clause = (f"a character model sheet showing the SAME single character in "
                      f"{views}. It is one character, not several.")

    # ---- subject_definitions ------------------------------------------------
    defs = ["subject_definitions:"]
    for s in subjects:
        e = book[s]
        if e.get("kind", "character") == "character":
            defs.append(
                f"{subj[s]} is {e['name']}, defined by {pic[s]} — {ref_clause} "
                f"Preserve the exact reference styling: {e['design']}.")
        else:
            defs.append(
                f"{subj[s]} is {e['name']}, defined by {pic[s]} — a single reference image "
                f"of the object on a plain background. Preserve its exact design, colors "
                f"and proportions: {e['design']}.")
    defs.append(
        f"{bg_subj} is the location, defined by <Picture 4> — a background plate of {env}. "
        f"It establishes the layout, color palette, lighting direction and depth of the "
        f"space, not a fixed camera framing.")
    # Audio references. dub/dub_keep_foley feed ONE clip -- the slice of the
    # recorded mix, which already contains every voice in the shot. clone feeds
    # one timbre sample PER SPEAKER, because there is no mix to slice and each
    # character's voice has to come from their own sample. Getting this wrong
    # voices the second speaker from the first speaker's sample.
    retention = shot.get("_retention", "")
    copying = retention in ("fully_copy", "partially_copy")

    def voice_owner(who: str) -> str:
        if who in subj:
            return f"{subj[who]} {spk[who]}"
        v = (book[who].get("voice") or book[who].get("name", "a speaker")).strip()
        return f"an off-screen voice that is {v} {spk[who]}"

    if shot["_policy"] in ("dub", "dub_keep_foley"):
        if copying:
            owners = ", ".join(voice_owner(w) for w in spoken)
            defs.append(
                f"<Audio 1> is the recorded dialogue for this shot, spoken by {owners}; "
                f"its exact words, voices and timing are reused.")
        else:
            defs.append(
                "<Audio 1> is the voice and timing reference for the spoken lines below.")
    elif shot["_policy"] == "clone":
        for i, who in enumerate(spoken, start=1):
            defs.append(
                f"<Audio {i}> is the voice-timbre reference for {speaker_label(who)}.")

    # ---- summary ------------------------------------------------------------
    tasks = ["reference generation"] + (
        (["audio reuse"] if copying else ["audio reference"]) if shot.get("_audio_ref") else [])
    beat = shot["action"][:220] or "A held moment in the scene."
    summary = f"summary:\n[{' + '.join(tasks)}] {beat}"

    # ---- retention_analysis -------------------------------------------------
    ret = ["retention_analysis:"]
    for s in subjects:
        e = book[s]
        if e.get("kind", "character") == "character":
            ret.append(
                f"{subj[s]} (appears in [Shot 1]): fully_preserved - facial identity, "
                f"hairstyle, body proportions, wardrobe and colors are retained from "
                f"{pic[s]} while allowing natural poses and expressions.")
        else:
            ret.append(
                f"{subj[s]} (appears in [Shot 1]): fully_preserved - the object's design, "
                f"colors, materials and proportions are retained from {pic[s]} while it is "
                f"handled and moved naturally.")
    # The environment marker scales with framing. Declaring partially_preserved
    # on a close-up over-weights a plate that occupies a small fraction of the
    # frame, and the plate then competes with the subject sheets for influence
    # in exactly the shots where identity matters most — the symptom being the
    # whole location rendered behind a face that should fill frame.
    if shot["size"] in ("close", "cu"):
        ret.append(
            f"{bg_subj} (appears in [Shot 1]): weak_reference - only the color palette, "
            f"lighting direction and material character of the location are retained; the "
            f"layout of the space is not reproduced and most of it falls outside the frame.")
    else:
        ret.append(
            f"{bg_subj} (appears in [Shot 1]): partially_preserved - the layout, color palette "
            f"and lighting direction of the location are retained while the camera framing and "
            f"visible portion of the space differ from the plate.")
    if shot.get("_audio_ref"):
        # Only claim the reference drives mouth movement when someone visible is
        # actually speaking on screen. On a pure-voiceover shot that phrasing
        # invites H3 to lip-sync a character who is not talking.
        on_screen_speech = any(not d.get("mode") and d["who"] in subj
                               for d in shot["dialogue"])
        sync = ""
        if on_screen_speech:
            movers = [subj[d["who"]] for d in shot["dialogue"]
                      if not d.get("mode") and d["who"] in subj]
            movers = list(dict.fromkeys(movers))
            others = any(d.get("mode") or d["who"] not in subj for d in shot["dialogue"])
            sync = (f", and the mouth movement of {' and '.join(movers)} is synchronized "
                    + ("only to their own lines in it" if others or len(movers) > 1
                       else "to its speech"))
        if retention == "fully_copy":
            ret.append(f"<Audio 1>: fully_copy - <Audio 1> is reused 1:1 as the target "
                       f"video's complete final audio track{sync}.")
        elif retention == "partially_copy":
            ret.append(f"<Audio 1>: partially_copy - the dialogue of <Audio 1> is copied "
                       f"exactly in words, voices and timing{sync}; the ambience and action "
                       f"sounds described below are added around it.")
        elif shot["_policy"] == "clone":
            for i, who in enumerate(spoken, start=1):
                ret.append(
                    f"<Audio {i}>: reference - {spk[who]} follows its voice timbre and "
                    f"delivery without copying the original signal.")
        else:
            ret.append(
                "<Audio 1>: reference - its vocal delivery, phrasing and timing guide the "
                + ("performance and mouth movement " if on_screen_speech
                   else "delivery and timing of the spoken lines ")
                + "without copying the original signal.")

    # ---- detailed_description ----------------------------------------------
    desc = ["detailed_description:", f"The target video is {look}."]
    body = f"[Shot 1] The scene takes place in {bg_subj}, {env}. {shot['action']}"
    cam = shot.get("camera", "").strip()
    body += f" The camera {cam}." if cam else " The camera holds a static shot with fixed composition."
    if shot.get("_continuation"):
        body += (" Continue the incoming action, pose, camera movement, lighting and object "
                 "states from the previous segment without resetting them.")
    desc.append(body)
    for d in shot["dialogue"]:
        deliv = f" {d['delivery']}," if d["delivery"] else ""
        src = " in the recorded voice from <Audio 1>," if copying else ""
        label = speaker_label(d["who"])
        mode = d.get("mode", "")
        if mode == "vo":
            # Exact phrase required by the spec, plus the lips-closed clause --
            # but only when the speaker is actually visible, since that clause
            # exists to stop H3 animating an on-screen mouth over narration.
            line = (f"{label} says in an off-screen voiceover{src.rstrip(',')}:{deliv} "
                    f"<d>[English] {d['line']}</d>")
            if d["who"] in subj:
                pron = (book[d["who"]].get("pronoun") or "their").strip()
                line += f" while {pron} lips remain completely closed."
            desc.append(line)
        elif mode == "os":
            desc.append(f"{label} says from off-screen, outside the frame,{src}{deliv} "
                        f"<d>[English] {d['line']}</d>")
        else:
            desc.append(f"{label} says,{src}{deliv} <d>[English] {d['line']}</d>")
    if shot.get("text"):
        desc.append(f'Visible on-screen text reads "{shot["text"]}".')

    sound = shot.get("sound", "").strip() or (
        "Continuous room tone and environmental ambience with synchronized physical action sounds")
    if retention == "fully_copy":
        sound = ("the only audio is the copied dialogue track from <Audio 1>; no ambience, "
                 "effects or music are added")
    elif retention == "partially_copy":
        sound = f"{sound[0].lower() + sound[1:]}, laid under the copied dialogue from <Audio 1>"
    music = shot.get("music", "").strip() or "N/A"

    return ["\n".join(defs), summary, "\n".join(ret), "\n".join(desc),
            f"overall_soundscape: {sound[0].upper() + sound[1:]}.",
            f"non_diegetic_music: {music}"]


# ---------------------------------------------------------------------------
# Compile
# ---------------------------------------------------------------------------

# Turbo LoRA per pass. The final pass uses lightx2v's 8-step Ref2V v1.0,
# which was distilled at 768p for exactly 8 steps. The proxy keeps the 4-step
# v0.1: a distilled LoRA is trained on a fixed set of timesteps, so it does
# best at its own step count, and the proxy wants 4 steps for speed.
# Override either with series.lora / proxy.lora in the bible.
FINAL_MODEL = "minimax_h3_ref2va_pruned_int8_convrot.safetensors"
FINAL_LORA = "minimax_h3_ref2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors"
FINAL_STEPS = 8
PROXY_LORA = "minimax_h3_ref2v_lightx2v_turbo_4step_v0.1_resized_avg_rank_20_bf16.safetensors"
PROXY_STEPS = 4


def compile_episode(ep: dict, bible: dict, proxy: bool) -> tuple[dict, dict]:
    s = bible["series"]
    fps = float(s.get("fps", 24))
    if proxy:
        p = bible.get("proxy", {})
        width, height = int(p.get("width", 480)), int(p.get("height", 272))
        steps = int(p.get("steps", PROXY_STEPS))
        lora = p.get("lora", PROXY_LORA)
        model = p.get("model", s.get("model", FINAL_MODEL))
    else:
        width, height = int(s.get("width", 1344)), int(s.get("height", 768))
        steps = int(s.get("steps", FINAL_STEPS))
        lora = s.get("lora", FINAL_LORA)
        model = s.get("model", FINAL_MODEL)

    for nm, v in (("width", width), ("height", height)):
        if v % 32:
            raise ValueError(
                f"{nm}={v} is not a multiple of 32 — H3 rejects it. "
                f"Nearest legal: {v // 32 * 32} or {(v // 32 + 1) * 32}.")

    book = bible["subjects"]
    audio_cfg = bible.get("audio", {})
    mode = audio_cfg.get("mode", "source_track")
    track = audio_cfg.get("track", "")
    # The recorded dialogue still times the picture when the proxy lets H3
    # invent the voices, and assemble lays it under the cut to check sync.
    recording = track

    # The proxy may run a different audio mode. The animatic exists to judge
    # staging, framing and pacing, and waiting on voice samples to see any of
    # that is a bad trade — so `proxy.audio_mode: generate` lets H3 invent the
    # voices for the cheap pass while the final still clones from your samples.
    # Seeds, lengths and subjects are unaffected, so the passes stay comparable.
    if proxy:
        pm = bible.get("proxy", {}).get("audio_mode")
        if pm:
            mode = pm
            if pm in ("generate", "generated", "generated_audio"):
                track = ""
    if mode == "source_track":
        speaking_policy = "dub_keep_foley"
    elif mode in ("generate", "generated", "generated_audio"):
        # No reference of any kind: H3 speaks the written lines in a voice of
        # its own choosing, guided only by each character's `voice` line in the
        # prompt. Needs no recordings and no voice samples. Expect the voice to
        # drift between shots — fine for a pilot, not for a long series.
        speaking_policy = "generate"
    else:
        speaking_policy = "clone"
    speaking_policy = bible.get("audio", {}).get("default_policy", speaking_policy)

    shots_out, warnings, needed = [], [], {}
    total_req = total_raw = 0

    blocked: dict[str, list[str]] = {}

    def need(path, kind, prompt, shot_id=None):
        if path:
            needed.setdefault(path, {"kind": kind, "prompt": prompt})
            if shot_id and shot_id not in blocked.setdefault(path, []):
                blocked[path].append(shot_id)

    def resolve_plate(key: str, where: str, shot_id: str | None = None) -> dict:
        """A location entry, validated, registered as a needed asset."""
        if key not in bible["locations"]:
            raise ValueError(
                f"{where}: location '{key}' is not in the bible "
                f"({', '.join(bible['locations'])})")
        entry = bible["locations"][key]
        if not entry.get("plate"):
            raise ValueError(
                f"location '{key}': needs a `plate` path. The background "
                f"plate is <Picture 4> in every shot there.")
        need(entry["plate"], "background plate",
             f"A background plate drawn as {bible['style']['look']}. An empty establishing "
             f"view of {entry['description']}. No characters, no props, no figures in frame — "
             f"the environment only. Wide framing that shows the layout of the space.",
             shot_id)
        return entry

    for seq in ep["sequences"]:
        seq_loc = resolve_plate(seq["location_key"], f"sequence {seq['id']}")
        # A sequence is a scene; a shot may name its own angle on that scene
        # with `plate:`. Cutting between angles of one room is the normal case,
        # so this lives on the shot rather than forcing a sequence break —
        # which would also re-seed every shot in it.
        for _sh in seq["shots"]:
            _loc = (resolve_plate(_sh["plate"], f"shot {_sh['id']}", _sh["id"])
                    if _sh.get("plate") else seq_loc)
            if _sh["id"] not in blocked.setdefault(_loc["plate"], []):
                blocked[_loc["plate"]].append(_sh["id"])

        for i, shot in enumerate(seq["shots"]):
            loc = (bible["locations"][shot["plate"]] if shot.get("plate") else seq_loc)
            # Characters first, then props/vehicles. Slots 1-3 only; slot 4 is
            # always the location plate.
            subjects = shot["cast"] + [p for p in shot["props"] if p not in shot["cast"]]
            shot["_subjects"] = subjects
            # A shot may legitimately have no visible subjects: an establishing
            # plate with voiceover over it. The location plate then fills all
            # four slots and becomes <Subject 1>. Only an empty shot is an error.
            if not subjects and not shot["dialogue"] and not shot["action"]:
                raise ValueError(
                    f"shot {shot['id']}: nothing in it. Add `who:`, `with:`, action "
                    f"text, or dialogue.")
            if len(subjects) > 3:
                over = len(subjects) - 3
                raise ValueError(
                    f"shot {shot['id']}: {len(subjects)} subjects ({', '.join(subjects)}) but "
                    f"only 3 slots — slot 4 is the background. Drop {over} from `with:`, or "
                    f"describe {over} in the action text instead of referencing "
                    f"{'them' if over > 1 else 'it'}.")

            # duration
            pace = shot.get("pace") or bible.get("speech", {}).get("pace", "normal")
            if pace not in SPEECH_RATE:
                raise ValueError(
                    f"bible speech.pace '{pace}' must be one of {sorted(SPEECH_RATE)}")
            need_sec = speech_seconds(shot["dialogue"], pace)

            if "audio_in" in shot:
                dur = shot["audio_out"] - shot["audio_in"]
                if not recording:
                    raise ValueError(
                        f"shot {shot['id']}: uses an `audio:` window but the bible has no "
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
            raw = snap_up(req)

            # Does the dialogue actually fit the window H3 will render?
            if shot["dialogue"]:
                held = raw / fps
                rate = forced_rate(shot["dialogue"], held)
                syl, gaps = pacing(shot["dialogue"])
                fits = snap_up(max(1, round(need_sec * fps))) / fps
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
            total_req += req
            total_raw += raw

            speaks = bool(shot["dialogue"])
            policy = shot.get("policy") or (speaking_policy if speaks else "generate")

            # Speakers can be off screen, so the voice sample a `clone` shot
            # needs is not necessarily one of the visible subjects.
            voices: list[str] = []
            for d in shot["dialogue"]:
                if d["who"] not in voices:
                    voices.append(d["who"])
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
                retention = (ret_req or bible.get("audio", {}).get("retention", "").lower()
                             or RETENTION_DEFAULT[policy])
                if retention not in RETENTIONS:
                    raise ValueError(f"bible audio.retention '{retention}' must be one of "
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
            shot["_retention"] = retention
            shot["_continuation"] = seq["continuous"] and i > 0

            # Always one panel. A 2- or 4-panel strip is a multi-figure image,
            # and H3 renders it as multiple people -- worst on wides. The face
            # panel carries identity where it matters; the three-quarter body
            # panel carries wardrobe and proportion everywhere else.
            panels = 1
            panel_view = ("face" if (len(subjects) == 1
                          and book[subjects[0]].get("kind", "character") == "character"
                          and shot["size"] in ("close", "cu")) else "body")

            for s in subjects:
                e = book[s]
                if e.get("kind", "character") == "character":
                    need(e.get("sheet"), "character sheet",
                         f"A character model sheet on a plain flat background: FOUR panels side "
                         f"by side in a single horizontal strip, left to right — three-quarter "
                         f"body, side profile full body, back view full body, and a "
                         f"head-and-shoulders facial close-up. The SAME character in all four. "
                         f"{e['design']}. Drawn as {bible['style']['look']}. "
                         f"Output 4096x1024 or larger.", shot["id"])

                else:
                    need(e.get("sheet"), f"{e.get('kind', 'prop')} reference",
                         f"A single clean three-quarter view of one object on a plain flat "
                         f"background, no scene around it. {e['design']}. Drawn as "
                         f"{bible['style']['look']}. Output 1024x1024 or larger.", shot["id"])

            if seq["continuous"] and i > 0 and 22 / raw > 0.15:
                warnings.append(
                    f"{shot['id']}: continuous chaining costs 22 of {raw} frames "
                    f"({22 / raw:.0%}). Shots this short are cheaper as separate cuts.")
            pad = raw - req
            if ("audio_in" not in shot and not shot.get("duration_auto")
                    and pad / raw > 0.15):
                warnings.append(
                    f"{shot['id']}: dur {dur:.2f}s snaps up to {raw / fps:.2f}s, wasting "
                    f"{pad} frames. Writing it as {raw / fps:.2f}s costs the same render.")

            # H3 accepts at most 3 standalone audio references, and clone mode
            # spends one per speaker.
            if policy == "clone" and len(voices) > 3:
                raise ValueError(
                    f"shot {shot['id']}: {len(voices)} speakers ({', '.join(voices)}) but "
                    f"clone mode feeds one voice sample each and H3 takes at most 3. "
                    f"Split the shot, or move a line to a neighbouring shot.")

            voice_refs = []
            if policy == "clone":
                for v in voices:
                    e = book[v]
                    need(e.get("voice_sample"), "voice sample",
                         f"A 5-15 second clean recording of {e['name']} speaking. "
                         f"Voice: {e.get('voice', 'as written in the bible')}.", shot["id"])
                    voice_refs.append({"subject": v, "sample": e.get("voice_sample", "")})

            entry = {
                "id": shot["id"],
                "sequence": seq["id"],
                "subjects": subjects,
                "background": loc["plate"],
                "size": shot["size"],
                "panels": panels,
                "panel_view": panel_view,
                "length": raw,
                "seed": stable_seed(ep["id"], seq["id"], shot["id"]),
                "steps": int(shot.get("steps", seq.get("steps", steps))),
                "audio_policy": policy,
                "audio_retention": retention,
                "voices": voices,
                "voice_subject": voices[0] if voices else "",
                "voice_refs": voice_refs,
                "prompt": build_prompt(shot, seq, bible, panels, panel_view),
            }
            # per-shot override: the shot beats the sequence beats the pass
            for _k in ("model", "lora"):
                _v = shot.get(_k) or seq.get(_k)
                if _v:
                    entry[_k] = _v
            if "audio_in" in shot:
                entry["audio_in"], entry["audio_out"] = shot["audio_in"], shot["audio_out"]
            else:
                entry["duration"] = round(dur, 3)
            shots_out.append(entry)

    doc = {
        # h3assemble names the review cut from these
        "episode": ep["id"],
        "title": ep.get("title", ""),
        "prompt_prefix": "",
        "defaults": {
            "width": width, "height": height, "steps": steps,
            "model": model, "lora": lora, "sheet_panels": 4,
            "audio_policy": "generate", "master_track": recording,
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
                     | {f"{sh['id']} overrides steps: {sh['steps']}"
                        for sh in shots_out if sh["steps"] != steps}):
        warnings.append(_o)

    m = re.search(r"(\d+)step", lora or "")
    if m and int(m.group(1)) != steps:
        warnings.append(f"{'proxy' if proxy else 'series'}.steps is {steps} but the LoRA "
                        f"{lora} is distilled for {m.group(1)} steps — set steps: {m.group(1)} "
                        f"or pick a matching lora")

    report = {
        "episode": ep["id"], "title": ep["title"],
        "mode": "proxy" if proxy else "final",
        "resolution": f"{width}x{height}", "steps": steps,
        "model": model, "lora": lora, "fps": fps,
        "shots": len(shots_out),
        "sequences": len(ep["sequences"]),
        "target_s": total_req / fps, "delivered_s": total_raw / fps,
        "pad_frames": total_raw - total_req,
        "policies": {p: sum(1 for s in shots_out if s["audio_policy"] == p)
                     for p in sorted({s["audio_policy"] for s in shots_out})},
        "needed": needed, "blocked_shots": blocked, "warnings": warnings,
    }
    return doc, report


# ---------------------------------------------------------------------------

def mmss(sec: float) -> str:
    m, s = divmod(sec, 60)
    return f"{int(m)}:{s:05.2f}"


SIZE_HINT = {
    "character sheet": "4096x1024 or larger (horizontal 4-panel strip)",
    "prop reference": "1024x1024 or larger",
    "vehicle reference": "1024x1024 or larger",
    "background plate": "1344x768 (the render resolution)",
    "voice sample": "5-15 seconds of clean speech, mono wav",
}


def render_todo(report: dict, root: str) -> str:
    """The asset work order.

    Every prompt here is built from the bible, so the wording that describes a
    character in their sheet prompt is the exact wording that goes into all of
    their shot prompts. Generating from a paraphrase is how a character ends up
    subtly wrong in every shot.
    """
    need = report["needed"]
    missing = [(p, v) for p, v in need.items()
               if not os.path.isfile(os.path.join(root, p))]
    have = len(need) - len(missing)
    blocked = report.get("blocked_shots", {})

    lines = [f"# Asset work order — {report['episode']} {report['title']}", "",
             f"{have}/{len(need)} on disk. Generate each missing asset, save it to the "
             f"path shown, then re-run h3build.", ""]
    if not missing:
        lines += ["Everything the episode needs is already on disk.", ""]
        return "\n".join(lines)

    lines += ["| # | Path | Kind | Target | Shots blocked |",
              "|---|---|---|---|---|"]
    for i, (path, v) in enumerate(missing, start=1):
        lines.append(f"| {i} | `{path}` | {v['kind']} | "
                     f"{SIZE_HINT.get(v['kind'], '—')} | {len(blocked.get(path, []))} |")
    lines += ["", "---", ""]

    for i, (path, v) in enumerate(missing, start=1):
        sh = blocked.get(path, [])
        lines += [f"## {i}. `{path}`",
                  f"**{v['kind']}** · target {SIZE_HINT.get(v['kind'], 'see prompt')}"
                  + (f" · blocks {len(sh)} shot(s): {', '.join(sh[:8])}"
                     f"{' …' if len(sh) > 8 else ''}" if sh else ""), "",
                  "```", v["prompt"].strip(), "```", ""]
    return "\n".join(lines)


def print_report(r: dict, root: str) -> None:
    print(f"\n  {r['episode']}  {r['title']}   [{r['mode']}]  "
          f"{r['resolution']}  {r['steps']} steps")
    print(f"  model        {r.get('model', '')}")
    print(f"  lora         {r.get('lora', '')}")
    print(f"  {'-' * 60}")
    print(f"  {r['sequences']} sequences, {r['shots']} shots")
    print(f"  runtime      {mmss(r['delivered_s'])}  "
          f"(scripted {mmss(r['target_s'])}, +{r['pad_frames']}f grid pad)")
    print(f"  audio        " + ", ".join(f"{n}x {p}" for p, n in r["policies"].items()))
    missing = [p for p in r["needed"] if not os.path.isfile(os.path.join(root, p))]
    have = len(r["needed"]) - len(missing)
    print(f"  references   {have}/{len(r['needed'])} on disk"
          + (f", {len(missing)} to make" if missing else ""))
    for w in r["warnings"]:
        print(f"    ! {w}")
    print()


def print_pacing(ep: dict, bible: dict, fps: float = 24.0) -> int:
    """Every dialogue shot measured against the rate it forces on the delivery."""
    default = bible.get("speech", {}).get("pace", "normal")
    rows, crammed, tight, gain = [], 0, 0, 0.0
    for seq in ep["sequences"]:
        for shot in seq["shots"]:
            if not shot["dialogue"]:
                continue
            pace = shot.get("pace") or default
            if "audio_in" in shot:
                dur = shot["audio_out"] - shot["audio_in"]
            elif shot.get("duration_auto"):
                dur = speech_seconds(shot["dialogue"], pace)
            else:
                dur = shot.get("duration", 0.0)
            held = snap_up(max(1, round(dur * fps))) / fps
            syl, _ = pacing(shot["dialogue"])
            rate = forced_rate(shot["dialogue"], held)
            fits = snap_up(max(1, round(speech_seconds(shot["dialogue"], pace) * fps))) / fps
            if rate > RATE_CEILING:
                verdict, mark = "CRAMMED", "!!"
                crammed += 1
                gain += fits - held
            elif rate > SPEECH_RATE[pace] + 0.15:
                verdict, mark = "tight", " !"
                tight += 1
                gain += max(0.0, fits - held)
            else:
                verdict, mark = "ok", "  "
            rows.append((mark, shot["id"], held, syl, rate, fits, verdict))

    if not rows:
        print("\n  no dialogue in this episode.\n")
        return 0
    print(f"\n  {'shot':10} {'held':>6} {'syl':>4} {'forced':>7} {'fits':>6}  verdict")
    print("  " + "-" * 52)
    for mark, sid, held, syl, rate, fits, verdict in rows:
        print(f"{mark}{sid:10} {held:6.2f} {syl:4} {rate:7.1f} {fits:6.2f}  {verdict}")
    print(f"\n  {crammed} crammed, {tight} tight, {len(rows) - crammed - tight} ok "
          f"of {len(rows)} dialogue shots")
    print(f"  pace '{default}' = {SPEECH_RATE[default]} syl/s, ceiling {RATE_CEILING}")
    if gain > 0:
        print(f"  giving every one of them room adds {gain:.2f}s of runtime")
    print("\n  Fix by lengthening (`dur:` or `dur: auto`), cutting syllables, or\n"
          "  splitting the shot. `pace: fast` marks a rush that is deliberate.\n")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("series", help="the bible, e.g. series.json")
    ap.add_argument("script", help="the episode script, e.g. ep01.md")
    ap.add_argument("-o", "--out", default=".", help="project root (where shotlist/ lives)")
    ap.add_argument("--proxy", action="store_true", help="emit the low-res animatic pass")
    ap.add_argument("--check", action="store_true", help="validate and report, write nothing")
    ap.add_argument("--pace", action="store_true",
                    help="report dialogue pacing per shot and write nothing")
    args = ap.parse_args()

    try:
        bible = load_bible(args.series)
        with open(args.script, encoding="utf-8") as fh:
            # parse -> story IR -> the dict the H3 compile code reads
            story = parse_story(fh.read(), subject_ids(bible), character_ids(bible),
                                series_info(bible))
        ep = legacy_episode(story)
        if args.pace:
            return print_pacing(ep, bible)
        doc, report = compile_episode(ep, bible, args.proxy)
    except (ScriptError, ValueError, KeyError) as exc:
        print(f"\n  error in {os.path.basename(args.script)}: {exc}\n", file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        print(f"\n  {exc}\n", file=sys.stderr)
        return 1

    print_report(report, args.out)
    if args.check:
        return 0

    os.makedirs(os.path.join(args.out, "shotlist"), exist_ok=True)

    name = f"shotlist{'_proxy' if args.proxy else ''}.json"
    sl = os.path.join(args.out, "shotlist", name)
    with open(sl, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)
    # The story IR: model-free and pass-free, so both passes write the same file.
    ir_path = os.path.join(args.out, "shotlist", "shots.json")
    with open(ir_path, "w", encoding="utf-8") as fh:
        json.dump(story.to_json(), fh, ensure_ascii=False, indent=2)
    # Suffix the proxy's work order the way the shotlist is suffixed. A proxy
    # pass can legitimately need fewer assets (generated voices need no
    # samples), and letting it overwrite the canonical list would quietly drop
    # items the final pass still requires.
    sfx = "_proxy" if args.proxy else ""
    todo = os.path.join(args.out, f"refs_todo{sfx}.md")
    with open(todo, "w", encoding="utf-8") as fh:
        fh.write(render_todo(report, args.out))

    # machine-readable twin, for driving a batch image run
    todo_json = os.path.join(args.out, f"refs_todo{sfx}.json")
    with open(todo_json, "w", encoding="utf-8") as fh:
        json.dump([
            {"path": p, "kind": v["kind"], "prompt": v["prompt"],
             "target": SIZE_HINT.get(v["kind"], ""),
             "exists": os.path.isfile(os.path.join(args.out, p)),
             "blocks_shots": report.get("blocked_shots", {}).get(p, [])}
            for p, v in report["needed"].items()
        ], fh, ensure_ascii=False, indent=2)

    print(f"  -> {sl}")
    print(f"  -> {ir_path}")
    print(f"  -> {todo}")
    print(f"  -> {todo_json}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
