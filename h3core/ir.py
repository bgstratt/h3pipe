"""
h3core.ir — the story IR: an episode as the script describes it, before any
model decision. Written to `shotlist/shots.json` by every build.

Durations are seconds as the script asked for them (no frame grid). Subjects
are series config ids in script order, with no reference slots. Nothing here names a
model, a prompt format or a pass, so one file serves every target and both
passes.

    Episode   id, title, series {fps, width, height}, sequences
    Sequence  id, location, continuous, overrides, source {line}, shots,
              target?, profile?
    Shot      id, cast, props, plate, size, camera, action, dialogue, sound,
              music, extras, text, timing, pace, audio, preserve, seed_key,
              overrides {model, lora, steps}, source {line, end_line}, unparsed,
              target?, profile?
    Line      speaker, mode ("on" | "vo" | "os"), delivery, line

`timing` is one of {"audio_in", "audio_out"} (a window on the recorded
dialogue), {"seconds"}, {"auto": true} (derive from the dialogue at `pace`), or
null (the script gave none; the target decides whether that is an error).
`pace` is the shot's own `pace:` or null for the series config's default; it applies to
every dialogue shot, not only `auto` ones.

`audio` is the script's explicit intent (generate | dub | dub_keep_foley |
clone) or null; the default is decided per target. `preserve` is how strictly a
recording is reused: strict | loose | style, or null.

`target` and `profile` are the script's `target:` / `profile:` lines (a
video target id, a series config profile name), or null. Both are omitted from
the JSON when null, so a script that uses neither gives the same shots.json it
always has. Which target a shot renders on is decided by the targets package
(series config default -> profiles -> sequence -> shot), not here.

`unparsed` holds script values the parser could not interpret, keyed by IR
field (e.g. {"steps": "eight"}). They are carried rather than rejected so the
target reports them exactly where and how it always has. It is omitted from
the JSON when empty.

`to_json()` returns plain JSON-able dicts; `from_json()` inverts it exactly.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field


def stable_seed(*parts: str) -> int:
    """A 63-bit seed from ids. Don't change: it's what makes untouched shots
    re-render identically."""
    h = hashlib.sha256("::".join(parts).encode("utf-8")).digest()
    return int.from_bytes(h[:8], "big") & 0x7FFF_FFFF_FFFF_FFFF


def _overrides(d: dict | None = None) -> dict:
    d = d or {}
    return {"model": d.get("model"), "lora": d.get("lora"), "steps": d.get("steps")}


def _put_choice(d: dict, node) -> None:
    """`target` and `profile`, only when set (see the module docstring)."""
    for k in ("target", "profile"):
        if getattr(node, k):
            d[k] = getattr(node, k)


@dataclass
class Line:
    speaker: str
    mode: str = "on"                 # "on" | "vo" | "os"
    delivery: str = ""
    line: str = ""

    def to_json(self) -> dict:
        return {"speaker": self.speaker, "mode": self.mode,
                "delivery": self.delivery, "line": self.line}

    @classmethod
    def from_json(cls, d: dict) -> "Line":
        return cls(speaker=d["speaker"], mode=d.get("mode", "on"),
                   delivery=d.get("delivery", ""), line=d.get("line", ""))


@dataclass
class Shot:
    id: str
    cast: list[str] = field(default_factory=list)
    props: list[str] = field(default_factory=list)
    plate: str = ""                  # location/angle key; the sequence's unless `plate:`
    size: str = "medium"
    camera: str | None = None
    action: str = ""
    dialogue: list[Line] = field(default_factory=list)
    sound: str | None = None
    music: str | None = None
    extras: str | None = None
    text: str | None = None
    timing: dict | None = None
    pace: str | None = None
    audio: str | None = None
    preserve: str | None = None
    seed_key: str = ""
    overrides: dict = field(default_factory=_overrides)
    source: dict = field(default_factory=dict)
    unparsed: dict = field(default_factory=dict)
    target: str | None = None
    profile: str | None = None

    def to_json(self) -> dict:
        d = {"id": self.id, "cast": list(self.cast), "props": list(self.props),
             "plate": self.plate, "size": self.size, "camera": self.camera,
             "action": self.action,
             "dialogue": [ln.to_json() for ln in self.dialogue],
             "sound": self.sound, "music": self.music, "extras": self.extras,
             "text": self.text,
             "timing": dict(self.timing) if self.timing is not None else None,
             "pace": self.pace, "audio": self.audio, "preserve": self.preserve,
             "seed_key": self.seed_key, "overrides": dict(self.overrides),
             "source": dict(self.source)}
        _put_choice(d, self)
        if self.unparsed:
            d["unparsed"] = dict(self.unparsed)
        return d

    @classmethod
    def from_json(cls, d: dict) -> "Shot":
        return cls(id=d["id"], cast=list(d.get("cast", [])), props=list(d.get("props", [])),
                   plate=d.get("plate", ""), size=d.get("size", "medium"),
                   camera=d.get("camera"), action=d.get("action", ""),
                   dialogue=[Line.from_json(x) for x in d.get("dialogue", [])],
                   sound=d.get("sound"), music=d.get("music"), extras=d.get("extras"),
                   text=d.get("text"),
                   timing=dict(d["timing"]) if d.get("timing") is not None else None,
                   pace=d.get("pace"), audio=d.get("audio"), preserve=d.get("preserve"),
                   seed_key=d.get("seed_key", ""), overrides=_overrides(d.get("overrides")),
                   source=dict(d.get("source", {})), unparsed=dict(d.get("unparsed", {})),
                   target=d.get("target"), profile=d.get("profile"))


@dataclass
class Sequence:
    id: str
    location: str
    continuous: bool = False
    overrides: dict = field(default_factory=_overrides)
    source: dict = field(default_factory=dict)
    shots: list[Shot] = field(default_factory=list)
    unparsed: dict = field(default_factory=dict)
    target: str | None = None
    profile: str | None = None

    def to_json(self) -> dict:
        d = {"id": self.id, "location": self.location, "continuous": self.continuous,
             "overrides": dict(self.overrides), "source": dict(self.source)}
        _put_choice(d, self)
        if self.unparsed:
            d["unparsed"] = dict(self.unparsed)
        d["shots"] = [s.to_json() for s in self.shots]
        return d

    @classmethod
    def from_json(cls, d: dict) -> "Sequence":
        return cls(id=d["id"], location=d["location"],
                   continuous=bool(d.get("continuous", False)),
                   overrides=_overrides(d.get("overrides")),
                   source=dict(d.get("source", {})),
                   shots=[Shot.from_json(s) for s in d.get("shots", [])],
                   unparsed=dict(d.get("unparsed", {})),
                   target=d.get("target"), profile=d.get("profile"))


@dataclass
class Episode:
    id: str
    title: str = ""
    series: dict = field(default_factory=dict)     # {fps, width, height}
    sequences: list[Sequence] = field(default_factory=list)

    def shots(self):
        """Every shot, in script order."""
        for sq in self.sequences:
            yield from sq.shots

    def to_json(self) -> dict:
        return {"episode": self.id, "title": self.title, "series": dict(self.series),
                "sequences": [s.to_json() for s in self.sequences]}

    @classmethod
    def from_json(cls, d: dict) -> "Episode":
        return cls(id=d["episode"], title=d.get("title", ""),
                   series=dict(d.get("series", {})),
                   sequences=[Sequence.from_json(s) for s in d.get("sequences", [])])

    def dumps(self) -> str:
        """The shots.json text."""
        return json.dumps(self.to_json(), ensure_ascii=False, indent=2)

    @classmethod
    def loads(cls, text: str) -> "Episode":
        return cls.from_json(json.loads(text))
