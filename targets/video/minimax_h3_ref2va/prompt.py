"""
The MiniMax H3 Ref2VA prompt: the six-section format from the H3 prompt-writing
spec (subject_definitions, summary, retention_analysis, detailed_description,
overall_soundscape, non_diegetic_music).

This is code on purpose (docs/PLAN.md: prompt formats are code, everything else
about a target is data). It reads the parser-shaped shot dict compile.py
prepares, with these compile-set keys:

    _subjects      the shot's subjects in slot order (<= 3)
    _policy        generate | dub | dub_keep_foley | clone
    _audio_ref     True unless the policy is generate
    _retention     fully_copy | partially_copy | reference | ""
    _continuation  a shot after the first in a continuous sequence

and two that only a render-anyway recompile sets (compile.compile_without),
for a shot whose reference files are missing:

    _unreferenced  on-screen subjects with no picture: named and described in
                   words, never given a <Subject N> / <Picture N>
    _no_plate      the plate is missing: the location is described in words,
                   and nothing mentions <Picture 4>

Neither is set by a build, so built prompts are exactly what they always were.

Stdlib only.
"""
from __future__ import annotations


COUNT_WORDS = {2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}


def _count(n: int) -> str:
    return COUNT_WORDS.get(n, str(n))


def _and(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + (", and " if len(items) > 2 else " and ") + items[-1]


def build_prompt(shot: dict, seq: dict, series_cfg: dict, panels: int,
                 panel_view: str = "body") -> list[str]:
    """Ref2VA six-section prompt.

    Slots 1-3 carry the shot's subjects -- characters first, then props and
    vehicles, in script order. Slot 4 always carries the location plate. The
    background is written as a proper <Subject> sourced from <Picture 4>,
    because H3's spec lists scenes and environments as Subject content; a bare
    <Picture N> means "use this exact frame as a keyframe", which is not what a
    location plate is for.
    """
    book = series_cfg["subjects"]
    look = series_cfg["style"]["look"]
    loc = series_cfg["locations"][shot.get("plate") or seq["location_key"]]
    env = loc["description"]

    subjects = shot["_subjects"]                      # ordered, <= 3
    subj = {s: f"<Subject {i + 1}>" for i, s in enumerate(subjects)}
    pic = {s: f"<Picture {i + 1}>" for i, s in enumerate(subjects)}
    bg_subj = f"<Subject {len(subjects) + 1}>"
    unref = list(shot.get("_unreferenced") or [])     # render anyway only
    no_plate = bool(shot.get("_no_plate"))            # render anyway only

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
        nothing to go on, and picks a different one next shot. So the series config's
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
        if who in unref:
            name = book[who].get("name", who)
            if first and voice:
                return f"{name}, with a voice that is {voice}, {spk[who]}"
            return f"{name} {spk[who]}"
        if first and voice:
            return f"An off-screen voice that is {voice} {spk[who]}"
        return f"The same off-screen voice {spk[who]}"

    # A reference image containing more than one figure gets drawn as more than
    # one person. One panel = one figure = one character on screen.
    #
    # The count of people in the SHOT is a separate statement, made once, at
    # the end of subject_definitions. It used to ride along on this clause, so every
    # character in a two-hander was defined with the sentence "Exactly one
    # person appears in this shot" — one contradiction per subject, in exactly
    # the shots where H3 resolves a contradiction by duplicating a referenced
    # character.
    extras = (shot.get("extras") or "").strip()
    if panels == 1:
        vname = ("a head-and-shoulders facial close-up" if panel_view == "face"
                 else "a three-quarter view of the whole body")
        ref_clause = (f"a single reference image of ONE character, {vname}. "
                      f"Exactly one person appears in that image.")
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
    for s in unref:
        e = book[s]
        defs.append(f"{e['name']} has no reference image and is drawn from this "
                    f"description: {e['design']}.")
    # Who is in frame, counted once and by name. "Character", not "person":
    # this pipeline's casts include a talking terrier and a raccoon, and the
    # reference clause above already calls a subject ONE character. A reference image containing
    # more than one figure gets drawn as more than one person, and a prompt
    # that names two subjects while insisting on one person in frame is the
    # same failure from the other end: H3 settles it by duplicating somebody.
    # `extras:` drops the count — a crowd has no number — and keeps the part
    # that matters, that the named people appear once each and the rest look
    # nothing like them.
    people = [subj[s] for s in subjects
              if book[s].get("kind", "character") == "character"]
    people += [book[s].get("name", s) for s in unref
               if book[s].get("kind", "character") == "character"]
    if people and extras:
        defs.append(f"{_and(people)} {'appears' if len(people) == 1 else 'each appear'} "
                    f"exactly once in this shot. The other people in frame are {extras}: "
                    f"unnamed background figures who must look nothing like "
                    f"{'them' if len(people) > 1 else 'that character'} and are never "
                    f"duplicates of {'them' if len(people) > 1 else 'that character'}.")
    elif len(people) == 1:
        defs.append(f"Exactly one character appears in this shot: {people[0]}.")
    elif people:
        defs.append(f"Exactly {_count(len(people))} characters appear in this shot: "
                    f"{_and(people)}. They are different characters, and never duplicates "
                    f"of one another.")
    if not no_plate:
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
        if who in unref:
            return f"{book[who].get('name', who)} {spk[who]}"
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
    #
    # But a close-up told to keep only the plate's colour loses to the sheet:
    # the sheet's plain studio backdrop becomes the background, the room
    # dissolves, and the sheet's pose comes with it. So a close-up keeps the
    # plate as a magnified crop -- the part of the room right behind the
    # subject, at close range -- and says the sheet's backdrop is not it.
    close = shot["size"] in ("close", "cu")
    if no_plate:
        pass
    elif close:
        ret.append(
            f"{bg_subj} (appears in [Shot 1]): partially_preserved - the background is a "
            f"zoomed-in crop of the location: the surfaces, objects, color palette and "
            f"lighting direction directly behind the subject are retained at close range, "
            f"while the rest of the space falls outside the frame. The plain backdrop of a "
            f"reference image is never used as the background.")
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
    body = (f"[Shot 1] The scene takes place in {env}. {shot['action']}" if no_plate else
            f"[Shot 1] The scene takes place in {bg_subj}, {env}. {shot['action']}")
    if close and not no_plate:
        body += (f" Behind the subject, the frame is filled by a magnified, softly focused "
                 f"part of {bg_subj}, recognizably the same place, never a plain studio "
                 f"backdrop.")
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
