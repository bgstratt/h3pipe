"""
What every image target shares (targets/image/<id>/): the wording of series
refs, the keyframe prompt, and the graph surgery for reference images.

Series refs (a character's views, a prop, a plate) are worded once, by the
krea2 target (targets/image/krea2/prompt.py): every image target describes
them the same way, so switching the refs target changes the picture, not
the brief.

A shot keyframe (`shot:<shot>:first|last`) is a still of one moment of a
shot, written from the story IR (keyframe_prompt): the look, the framing and
the location, who is in frame (from the series config's `design`), and the
action at that moment: how the shot opens ("first") or how it ends, the
action finished ("last"). An edit target (capabilities.mode "edit") also
reads reference images: the picked character views (the face on a
single-character close-up, else the body) and the plate, up to its
`max_refs`, and the prompt names each one (reference_intro).

The reference images reach an edit graph through a chain the workflow has
once, titled "Reference 1 ..." (its LoadImage "Reference 1"); a job with N
references gets N chains (each one's conditioning feeding the next), and a
job with none gets the chain cut out (reference_chains).

Stdlib only.
"""
from __future__ import annotations

import copy
import re

from targets.image.krea2 import prompt as KP

SIZE_WORDS = {"close": "close-up", "cu": "close-up", "medium": "medium shot",
              "ms": "medium shot", "wide": "wide shot", "ws": "wide shot"}
FACE_SIZES = ("close", "cu")


def ref_prompt(target, req, series_cfg: dict) -> str:
    """A series ref's wording (refs_todo, generate): krea2's, for every image
    target."""
    return KP.ref_prompt(target, req, series_cfg)


# ---------------------------------------------------------------------------
# the keyframe prompt
# ---------------------------------------------------------------------------

def _sentence(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    text = text[0].upper() + text[1:]
    return text if text[-1] in ".!?\"'" else text + "."


def _and(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + (", and " if len(items) > 2 else " and ") + items[-1]


def _article(phrase: str) -> str:
    return ("an " if phrase[:1].lower() in "aeiou" else "a ") + phrase


def sentences(text: str) -> list[str]:
    """Prose split into sentences (on . ! ? followed by a space)."""
    parts = re.split(r"(?<=[.!?])\s+", (text or "").strip())
    return [p.strip() for p in parts if p.strip()]


def moment(action: str, which: str) -> str:
    """The action at one end of the shot. "first": its opening sentence, as
    the shot begins (the action about to happen); "last": its closing
    sentence, finished (the end state it leaves). One sentence serves both
    ends, framed by `which`."""
    s = sentences(action)
    if not s:
        return ""
    if which == "first":
        text = s[0].rstrip(".!?")
        rest = " ".join(s[1:])
        out = (f"The moment the shot opens, at the very start of the action: {text}"
               f"{', just beginning' if len(s) == 1 else ''}.")
        if rest:
            out += " (What follows, not yet shown: " + rest.rstrip(".!?") + ".)"
        return out
    text = s[-1].rstrip(".!?")
    return (f"The moment the shot ends, after the action is over: {text}, completed, "
            f"everything come to rest where the action leaves it.")


def ref_label(ref: dict) -> str:
    """How a reference image is named in the prompt: "Ada" / "the kettle"
    for a subject, "the <name> background" for a plate."""
    if ref.get("role") == "plate":
        return f"the background ({ref.get('name') or ref.get('location')})"
    return ref.get("name") or ref.get("subject") or "a reference"


def composite_intro(r: dict, n: str) -> str:
    """How a composed reference (one image: the figures pasted over the
    plate) is named: who is in it, what to keep from each, and that the
    pose and framing are this shot's, not the collage's."""
    parts = r.get("parts") or []
    figures = [p for p in parts if p.get("role") != "plate"]
    plate = next((p for p in parts if p.get("role") == "plate"), None)
    names = _and([ref_label(p) for p in figures])
    people = any(p.get("kind", "character") == "character" for p in figures)
    keep = "face, body, clothes and colours" if people else "shape and colours"
    who = "each of them" if len(figures) > 1 else ("them" if people else "it")
    if figures and plate:
        return (f"{n.capitalize()} shows {names} in front of {ref_label(plate)}: draw "
                f"{who} exactly as in {n} (the same {keep}), and use its background's "
                f"setting, layout, colours and light for the scene. {n.capitalize()} is only "
                f"a reference collage: pose and frame {who} for this shot.")
    if figures:
        return (f"{n.capitalize()} shows {names}: draw {who} exactly as in {n} (the same "
                f"{keep}), posed and framed for this shot.")
    return (f"{n.capitalize()} is the background plate: use its setting, layout, colours "
            f"and light for the scene.")


def reference_intro(refs: list[dict], word: str = "image") -> str:
    """The sentences that name each reference image by its number, and what
    to take from it: a character's or object's look, the plate's setting."""
    if not refs:
        return ""
    out = []
    for i, r in enumerate(refs, 1):
        n = f"{word} {i}" if len(refs) > 1 else f"the {word}"
        if r.get("role") == "composite":
            out.append(composite_intro(r, n))
            continue
        if r.get("role") == "plate":
            out.append(f"{n.capitalize()} is the background plate: use its setting, layout, "
                       f"colours and light for the scene.")
        else:
            what = "character" if r.get("kind", "character") == "character" else "object"
            out.append(f"{n.capitalize()} is {ref_label(r)}: draw this {what} exactly as in "
                       f"{n} (the same {'face, body, clothes' if what == 'character' else 'shape'}"
                       f" and colours), posed for this shot.")
    return " ".join(out)


def keyframe_prompt(shot, seq, series_cfg: dict, which: str,
                    refs: list[dict] | None = None, word: str = "image") -> str:
    """The still for `shot`'s `which` ("first" | "last") keyframe, from its
    IR (`shot` an h3core.ir.Shot, `seq` its Sequence): the look, the framing
    of the location, who is in frame and what they look like, and the action
    at that moment. `refs` (an edit target's reference images, in order:
    {"role": "subject" | "plate", "subject"?, "location"?, "name", "kind"})
    are named first. No dialogue text: an image model would letter it."""
    book = series_cfg.get("subjects", {})
    look = ((series_cfg.get("style") or {}).get("look") or "").strip().rstrip(".")
    loc_key = shot.plate or (seq.location if seq is not None else "")
    loc = series_cfg.get("locations", {}).get(loc_key, {})
    env = (loc.get("description") or "").strip().rstrip(".")
    parts = []
    intro = reference_intro(refs or [], word)
    if intro:
        parts.append(intro)
    end = "first" if which == "first" else "last"
    size = SIZE_WORDS.get(shot.size, "medium shot")
    parts.append(_sentence(f"the {end} frame of {_article(size)}"
                           f"{' of ' + env if env else ''}, one still picture"))

    def name_of(s: str) -> str:
        return (book.get(s) or {}).get("name", s)

    on_screen = list(shot.cast) + [p for p in shot.props if p not in shot.cast]
    people = [name_of(s) for s in shot.cast
              if (book.get(s) or {}).get("kind", "character") == "character"]
    things = [name_of(s) for s in on_screen if name_of(s) not in people]
    frame = framing(size, people, things)
    if frame:
        parts.append(frame)
    if look:
        parts.append(f"Drawn as {look}, framed as {_article(size)}.")
    plain = []
    for s in on_screen:
        design = ((book.get(s) or {}).get("design") or "").strip().rstrip(".")
        if design:
            parts.append(_sentence(f"{name_of(s)} is {design}"))
        else:
            plain.append(name_of(s))
    if plain:
        parts.append(_sentence(f"{_and(plain)} {'is' if len(plain) == 1 else 'are'} in frame"))
    if shot.extras:
        parts.append(_sentence(f"Also in frame are {shot.extras}, unnamed background figures"))
    if shot.action:
        parts.append(moment(shot.action, which))
    speakers = []
    for d in shot.dialogue:
        if d.mode == "on" and d.speaker in shot.cast and name_of(d.speaker) not in speakers:
            speakers.append(name_of(d.speaker))
    if speakers:
        verb = "is about to speak" if which == "first" else "has just spoken"
        parts.append(_sentence(f"{_and(speakers)} {verb if len(speakers) == 1 else verb.replace('is', 'are').replace('has', 'have')}"))
    if not on_screen:
        parts.append("Nobody named is in frame.")
    parts.append("No text, captions, speech bubbles or borders.")
    parts.append(_sentence(f"framing: {_article(size)}"))
    return " ".join(p for p in parts if p)


def framing(size: str, people: list[str], things: list[str]) -> str:
    """The framing, said plainly (an image model left to itself draws a
    medium-wide view whatever the size word): a close-up fills the frame
    with the face, a medium shot is waist up, a wide shot shows the whole
    place with small full-length figures. `people` are the characters'
    names, `things` the props' and vehicles'."""
    if size == "close-up":
        if people:
            faces = (f"{people[0]}'s face fills" if len(people) == 1
                     else f"{_and(people)}'s faces fill")
            return (f"Framing: a close-up. {faces} the frame, large: from the chin to the "
                    f"top of the head, cut off at the shoulders. No full body, no wide view "
                    f"of the room.")
        if things:
            return (f"Framing: a close-up. {_and(things)} {'fills' if len(things) == 1 else 'fill'}"
                    f" the frame, large and close. No wide view of the room.")
        return "Framing: a close-up of one detail of the place, large and close. No wide view."
    if size == "medium shot":
        if people:
            return (f"Framing: a medium shot. {_and(people)} seen from the waist up, filling "
                    f"most of the frame's height.")
        return ""
    if people:
        return (f"Framing: a wide shot. The whole place in view, {_and(people)} small and "
                f"full-length in it.")
    return "Framing: a wide shot. The whole place in view."


# ---------------------------------------------------------------------------
# reference images in the graph
# ---------------------------------------------------------------------------

CHAIN_PREFIX = "Reference 1"


def _title(node: dict) -> str:
    return (node.get("_meta") or {}).get("title") or ""


def _consumers(g: dict, nid: str, slot: int = 0) -> list[tuple[str, str]]:
    return [(k, name) for k, v in g.items() for name, val in v["inputs"].items()
            if val == [nid, slot]]


def chain_nodes(g: dict, n: int = 1) -> dict[str, str]:
    """{role: node id} of reference chain `n` (nodes titled "Reference n" or
    "Reference n <role>")."""
    out = {}
    pre = f"Reference {n}"
    for k, v in g.items():
        t = _title(v)
        if t == pre:
            out["image"] = k
        elif t.startswith(pre + " "):
            out[t[len(pre) + 1:]] = k
    return out


def reference_chains(g: dict, names: list[str], links: dict[str, str]) -> None:
    """Give an edit graph one reference chain per image in `names` (the names
    ComfyUI's LoadImage reads, in order).

    The workflow has chain 1: a LoadImage titled "Reference 1" and the nodes
    after it titled "Reference 1 <role>". `links` maps each conditioning
    role of the chain (e.g. "positive", "negative") to the input of that
    node that takes the conditioning it adds to (e.g. "conditioning"). Chain
    k + 1 is a copy of chain k whose conditioning roles read chain k's, and
    whatever read chain k's outputs reads the last chain's. With no images
    the chain is cut out: whatever read a role reads what fed it."""
    first = chain_nodes(g, 1)
    if "image" not in first:
        raise ValueError("an edit workflow needs a LoadImage titled 'Reference 1'")
    if not names:
        for role, inp in links.items():
            nid = first.get(role)
            if nid is None:
                continue
            src = g[nid]["inputs"].get(inp)
            for k, name in _consumers(g, nid):
                g[k]["inputs"][name] = copy.deepcopy(src)
        for nid in first.values():
            g.pop(nid, None)
        return
    g[first["image"]]["inputs"]["image"] = names[0]
    prev = first
    # every consumer outside the chain of each conditioning role's output
    outside = {role: [(k, name) for k, name in _consumers(g, prev[role])
                      if k not in prev.values()]
               for role in links if role in prev}
    for i, name in enumerate(names[1:], start=2):
        ids = {role: f"h3ref{i}_{role.replace(' ', '_')}" for role in first}
        for role, old in first.items():
            node = copy.deepcopy(g[old])
            node["_meta"] = {"title": f"Reference {i}" + ("" if role == "image" else f" {role}")}
            for inp, val in node["inputs"].items():
                if isinstance(val, list) and len(val) == 2 and isinstance(val[0], str):
                    # inside the chain: this chain's copy
                    back = {v: r for r, v in first.items()}
                    if val[0] in back:
                        node["inputs"][inp] = [ids[back[val[0]]], val[1]]
            if role in links:
                node["inputs"][links[role]] = [prev[role], 0]
            if role == "image":
                node["inputs"]["image"] = name
            g[ids[role]] = node
        prev = ids
    for role, cons in outside.items():
        for k, name in cons:
            g[k]["inputs"][name] = [prev[role], 0]
