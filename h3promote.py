#!/usr/bin/env python3
"""
h3promote.py — move editor overrides into the authored files ("promote"),
then drop them (docs/API.md, "Phase 9a").

    python h3.py promote Shows\\ep05                 # the plan: what would move, the diffs
    python h3.py promote Shows\\ep05 sh020           # only sh020's overrides
    python h3.py promote Shows\\ep05 --all           # promote everything that can be
    python h3.py promote Shows\\ep05 --item shot:sh020:model --item episode:target
    python h3.py promote Shows\\ep05 --all --dry-run # the plan, even with --all

What moves where (anything else stays in overrides.json, listed under `left`
with the reason):

    shot `target`                  -> a `target:` line in the shot's block
    shot `model` / `loras` / `steps` (the block of the target the shot renders on)
                                   -> `model:` / `lora:` / `steps:` lines. A script
                                      line sets both passes, so the value must be
                                      what both passes render (the same override in
                                      both, or the other pass already renders it);
                                      `lora:` holds one LoRA (or `none`)
    episode `target`               -> series config `series.target` (not while its
                                      `series` / `proxy` blocks set model, LoRA or
                                      steps: those would then apply to the new target)
    episode `refs_target` / `keyframe_target`
                                   -> series config `refs.target` / `refs.keyframe_target`
    a ref's prompt override        -> the subject's `design` / the location's
                                      `description`, when the override is the built
                                      prompt with only that sentence changed (a
                                      character: all four views, agreeing)

Prompts (compiled text), seeds, negatives, notes, model_low, and a ref's seed,
model, LoRAs, steps, note and image target have no faithful place in the
script or the series config.

Every plan is checked before it is offered: the promoted files are parsed and
compiled in memory and every shot must render with the same target, model,
LoRAs and steps in both passes as it does now (h3jobs' precedence: the build,
then the override). A ref's generate prompt must come out as the override's
text. What fails that check goes to `left`.

Script edits are line-level: an existing `key:` line in the shot's own block is
replaced, else a new line goes after the shot's last `key:` line (or right
after its `##` line); sequence headers are never touched. The series config is
rewritten as JSON with a 2-space indent, key order and non-ASCII text kept.
Both files keep their line endings and BOM and get a _history/ copy
(h3source).

Stdlib only.
"""
from __future__ import annotations

import argparse
import copy
import difflib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import h3edit as E  # noqa: E402
import h3jobs as J  # noqa: E402
import h3source as H  # noqa: E402
import h3takes as T  # noqa: E402
import targets as TG  # noqa: E402

SHOT_LINE = {"target": "target", "model": "model", "loras": "lora", "steps": "steps"}
PASS_PROMOTE = ("model", "loras", "steps")
EPISODE_DEST = {"target": ("series", "target"), "refs_target": ("refs", "target"),
                "keyframe_target": ("refs", "keyframe_target")}
PASS_BLOCK_KEYS = ("model", "lora", "steps")

SHOT_LEFT = {
    "prompt": "compiled prompt text has no script form (edit the shot's action, dialogue "
              "or camera lines instead)",
    "seed": "a seed has no script form: keep it by picking the take",
    "negative": "a negative prompt is per target and pass, not a script line",
    "note": "a note has no place in the script",
    "model_low": "the script has no line for a two-stage target's low-noise model",
}
REF_LEFT = {
    "seed": "a seed has no place in the series config: keep it by picking the take",
    "model": "the series config has no per-ref model",
    "loras": "the series config has no per-ref LoRAs",
    "steps": "the series config has no per-ref steps",
    "note": "a note has no place in the series config",
    "target": "the series config has no per-ref image target (its `refs.target` is every "
              "ref's)",
}


class PromoteError(H.SourceError):
    pass


# ---------------------------------------------------------------------------
# the episode as it is now
# ---------------------------------------------------------------------------

class State:
    """Both authored files, parsed; the overrides; the refs' series."""

    def __init__(self, ep: str):
        from h3core.series_config import series_config_from
        from h3core.story import ScriptError
        import h3refs as R
        self.ep = os.path.abspath(ep)
        self.script = H.read_source(self.ep, "script")
        self.series = H.read_source(self.ep, "series")
        try:
            self.raw = json.loads(self.series.text)
        except json.JSONDecodeError as e:
            raise PromoteError(400, f"series.json is not valid JSON (line {e.lineno}): "
                                    f"fix it before promoting") from None
        try:
            self.cfg = series_config_from(copy.deepcopy(self.raw))
            self.story = parse(self.script.text, self.cfg)
        except ScriptError as e:
            raise PromoteError(400, f"the script doesn't parse (line {e.line_no}: {e.msg}): "
                                    f"fix it before promoting") from None
        except ValueError as e:
            raise PromoteError(400, f"series.json can't be loaded ({e}): fix it before "
                                    f"promoting") from None
        self.ov = T.load_overrides(self.ep)
        self.home = os.path.dirname(os.path.abspath(self.series.path))
        self.s = R.Series(self.ep, self.series.path, self.home, self.cfg)
        self.ref_ov = {h: R.load_overrides(h) for h in dict.fromkeys([self.home, self.ep])}


def parse(text: str, cfg: dict):
    from h3core.series_config import character_ids, series_info, subject_ids
    from h3core.story import parse_story
    return parse_story(text, subject_ids(cfg), character_ids(cfg), series_info(cfg))


# ---------------------------------------------------------------------------
# what a shot renders with (target, model, LoRAs, steps), per pass
# ---------------------------------------------------------------------------

def _loras(v) -> list | None:
    if v is None:
        return None
    return [{"name": lo.get("name"), "strength": float(lo.get("strength", 1.0))} for lo in v]


def script_targets(story, cfg: dict) -> dict[str, str | None]:
    """{shot: the target its script lines or profiles give it, or None}."""
    profiles = TG.series_profiles(cfg)
    out = {}
    for sq in story.sequences:
        seq = {"id": sq.id, "profile": sq.profile, "target": sq.target}
        for sh in sq.shots:
            shot = {"id": sh.id, "profile": sh.profile, "target": sh.target}
            out[sh.id] = TG.layered(TG.render_layers(cfg, seq, shot, profiles), "target")
    return out


def effective_targets(story, cfg: dict, ov: dict) -> dict[str, str]:
    """{shot: the video target its next render uses}: the override's, the
    script's, the episode's (overrides.json), the build's (h3jobs.target_choice)."""
    built = TG.shot_targets(story, cfg)
    script = script_targets(story, cfg)
    return {sid: (T.shot_target(ov, sid) or script[sid] or T.episode_target(ov) or built[sid])
            for sid in built}


def render_values(story, cfg: dict, ov: dict) -> dict[tuple[str, str], dict]:
    """{(shot, pass): {"target", "model", "loras", "steps"}} a render of each
    shot would use now (seed and prompt aside), as h3jobs.plan_job picks them:
    the shot compiled for its effective target, then the override for that
    target. A shot that can't be compiled there has {"error": ...}."""
    targets = effective_targets(story, cfg, ov)
    out = {}
    for ps in T.PASSES:
        groups: dict[str, set] = {}
        for sid, tid in targets.items():
            groups.setdefault(tid, set()).add(sid)
        for tid, ids in groups.items():
            try:
                t = TG.load_target(tid, "video")
            except TG.TargetError as e:
                for sid in ids:
                    out[(sid, ps)] = {"target": tid, "error": str(e)}
                continue
            entries = {}
            try:
                doc, _ = t.compile_episode(story, cfg, ps, only=ids)
                entries = {e["id"]: (doc, e) for e in doc["shots"]}
            except Exception:
                for sid in ids:                          # one at a time, as a retarget does
                    try:
                        doc, _ = t.compile_episode(story, cfg, ps, only={sid})
                        entries[sid] = (doc, doc["shots"][0])
                    except Exception as e:
                        out[(sid, ps)] = {"target": tid, "error": f"{e}"}
            for sid, (doc, entry) in entries.items():
                model, loras, steps = J.built_values(entry, doc.get("defaults", {}))
                o = T.shot_override(ov, sid, ps, tid)
                out[(sid, ps)] = {"target": tid, "model": o.get("model", model),
                                  "loras": _loras(o.get("loras", loras)),
                                  "steps": int(o.get("steps", steps))}
    return out


def _show(v) -> str:
    if isinstance(v, list):
        return ", ".join(f"{lo['name']}@{lo.get('strength', 1.0):g}" for lo in v) or "no LoRA"
    return "none" if v in (None, "") else str(v)


# ---------------------------------------------------------------------------
# script and series config edits
# ---------------------------------------------------------------------------

META = re.compile(r"^([a-z_]+)\s*:\s*(.*)$")


def lora_line(loras: list) -> str | None:
    """The `lora:` value for a LoRA list, or None when one line can't say it."""
    if not loras:
        return "none"
    if len(loras) > 1:
        return None
    lo = loras[0]
    name, strength = lo.get("name") or "", float(lo.get("strength", 1.0))
    if not name or name != name.strip() or "\n" in name:
        return None
    return name if strength == 1.0 else f"{name}:{strength:g}"


def edit_script(text: str, spans: dict[str, tuple[int, int]],
                edits: dict[str, list[tuple[str, str]]]) -> str:
    """`text` with each shot's `key: value` lines set: an existing line of
    that key in the shot's own block replaced (the last, which is the one the
    parser keeps), else inserted after the `key:` lines that open the block
    (or its `##` line), so a new line never lands below the dialogue or a
    trailing `sound:`/`music:`. Nothing else changes."""
    from h3core.story import META_KEYS
    lines = text.split("\n")
    for sid in sorted(edits, key=lambda k: spans[k][0], reverse=True):
        first, last = spans[sid]
        found, last_meta, leading = {}, first, True
        for n in range(first + 1, last + 1):
            raw = lines[n - 1].rstrip()
            if not raw.strip() or raw.strip().startswith("//"):
                continue
            m = META.match(raw)
            if m and m.group(1) in META_KEYS:
                found[m.group(1)] = n
                if leading:
                    last_meta = n
            else:
                leading = False
        inserts = []
        for key, value in edits[sid]:
            if key in found:
                lines[found[key] - 1] = f"{key}: {value}"
            else:
                inserts.append(f"{key}: {value}")
        lines[last_meta:last_meta] = inserts
    return "\n".join(lines)


def key_line(text: str, span: tuple[int, int], key: str) -> int | None:
    lines = text.split("\n")
    hit = None
    for n in range(span[0] + 1, span[1] + 1):
        m = META.match(lines[n - 1].rstrip())
        if m and m.group(1) == key:
            hit = n
    return hit


def dump_series(raw: dict, like: str) -> str:
    """The series config as the promote writes it: 2-space indent, key order
    and non-ASCII text kept, a final newline if the file had one."""
    return json.dumps(raw, indent=2, ensure_ascii=False) + ("\n" if like.endswith("\n") else "")


def formatted(text: str) -> bool:
    """Whether a series config text is already what dump_series writes."""
    try:
        return dump_series(json.loads(text), text).rstrip("\n") == text.rstrip("\n")
    except ValueError:
        return False


def unified(old: str, new: str, name: str) -> str:
    if old == new:
        return ""
    return "".join(difflib.unified_diff(old.splitlines(keepends=True),
                                        new.splitlines(keepends=True),
                                        fromfile=f"a/{name}", tofile=f"b/{name}"))


# ---------------------------------------------------------------------------
# the plan
# ---------------------------------------------------------------------------

def _left(scope: str, field: str, reason: str, **where) -> dict:
    return dict({"scope": scope}, **{k: v for k, v in where.items() if v is not None},
                field=field, reason=reason)


def _script_order(story) -> list[str]:
    return [sh.id for sq in story.sequences for sh in sq.shots]


def shot_candidates(st: State, before: dict, only_shot: str | None) -> tuple[list, list]:
    """(items, left) for the shots' overrides."""
    items, left = [], []
    order = _script_order(st.story)
    targets = effective_targets(st.story, st.cfg, st.ov)
    shots = st.ov.get("shots", {})
    for sid in sorted(shots, key=lambda k: (order.index(k) if k in order else len(order), k)):
        if only_shot and sid != only_shot:
            continue
        block = shots[sid]
        if not isinstance(block, dict):
            continue
        if sid not in order:
            for f in _fields(block):
                left.append(_left("shot", f, f"{sid} is not in the script", shot=sid))
            continue
        eff = targets[sid]
        t = T.shot_target(st.ov, sid)
        if t:
            items.append({"id": f"shot:{sid}:target", "scope": "shot", "shot": sid,
                          "field": "target", "value": t, "dest": "script",
                          "summary": f"{sid}: `target: {t}`",
                          "_edit": ("target", t)})
        for tid, tb in block.items():
            if not isinstance(tb, dict):
                continue
            if tid != eff:
                for f in _fields({tid: tb}):
                    left.append(_left("shot", f, f"written for {tid}, and {sid} renders on "
                                                 f"{eff} now: a script line would apply it "
                                                 f"to another model", shot=sid))
                continue
            for f in T.SHOT_FIELDS:
                if tb.get(f) is not None:
                    left.append(_left("shot", f, SHOT_LEFT[f], shot=sid))
            for ps in T.PASSES:
                for f in ("prompt", "negative", "model_low"):
                    if (tb.get(ps) or {}).get(f) is not None:
                        left.append(_left("shot", f, SHOT_LEFT[f], shot=sid, **{"pass": ps}))
            for f in PASS_PROMOTE:
                vals = {ps: (tb.get(ps) or {}).get(f) for ps in T.PASSES}
                vals = {ps: v for ps, v in vals.items() if v is not None}
                if not vals:
                    continue
                item, why = _pass_item(sid, f, vals, before)
                if item:
                    items.append(item)
                else:
                    left.append(_left("shot", f, why, shot=sid))
    return items, left


def _fields(block: dict) -> list[str]:
    """The user-visible fields a shot's override block sets."""
    out = []
    if isinstance(block.get("target"), str) and block["target"]:
        out.append("target")
    for tid, tb in block.items():
        if not isinstance(tb, dict):
            continue
        out += [f for f in T.SHOT_FIELDS if tb.get(f) is not None]
        for ps in T.PASSES:
            out += [f for f, v in (tb.get(ps) or {}).items()
                    if f != "base_hash" and v is not None]
    return list(dict.fromkeys(out))


def _pass_item(sid: str, f: str, vals: dict, before: dict) -> tuple[dict | None, str]:
    key = SHOT_LINE[f]
    norm = {ps: (_loras(v) if f == "loras" else v) for ps, v in vals.items()}
    distinct = {json.dumps(v, sort_keys=True) for v in norm.values()}
    if len(distinct) > 1:
        return None, (f"the final and proxy overrides differ ({_show(norm['final'])} / "
                      f"{_show(norm['proxy'])}); a `{key}:` line sets both passes")
    value = next(iter(norm.values()))
    for ps in T.PASSES:
        if ps in norm:
            continue
        now = before.get((sid, ps), {})
        if "error" in now or now.get(f) != value:
            other = [p for p in T.PASSES if p in norm][0]
            return None, (f"only the {other} pass overrides {f}; a `{key}:` line sets both "
                          f"passes, and the {ps} pass renders "
                          f"{_show(now.get(f)) if 'error' not in now else 'nothing'} now")
    if f == "loras":
        line = lora_line(value)
        if line is None:
            return None, (f"a `lora:` line holds one LoRA and this override stacks "
                          f"{len(value)} ({_show(value)}): put them in a profile's `loras`")
    elif f == "steps":
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            return None, f"steps {value!r} isn't a whole number of 1 or more"
        line = str(value)
    else:
        if not isinstance(value, str) or not value.strip() or value != value.strip() \
                or "\n" in value:
            return None, f"model {value!r} can't be written as a `model:` line"
        line = value
    passes = "both passes" if len(norm) == 2 else f"the {next(iter(norm))} pass"
    return ({"id": f"shot:{sid}:{f}", "scope": "shot", "shot": sid, "field": f,
             "value": value, "dest": "script",
             "summary": f"{sid}: `{key}: {line}` (the override of {passes})",
             "_edit": (key, line)}, "")


def episode_candidates(st: State) -> tuple[list, list]:
    items, left = [], []
    for f, (block, key) in EPISODE_DEST.items():
        v = T.episode_field(st.ov, f)
        if not v:
            continue
        where = f"`{block}.{key}`"
        if f == "target":
            old = (st.raw.get("series") or {}).get("target") or TG.DEFAULT_VIDEO_TARGET
            sets = [f"{b}.{k}" for b in ("series", "proxy")
                    for k in PASS_BLOCK_KEYS if (st.raw.get(b) or {}).get(k) is not None]
            if sets and old != v:
                left.append(_left("episode", f, (
                    f"the series config's {', '.join(sets)} are written for {old}: as "
                    f"{where} they would apply to {v}'s renders too")))
                continue
        summary = f"series config {where}: {v}"
        if st.home != st.ep:
            summary += f" (the series config in {os.path.basename(st.home)} is shared: " \
                       f"every episode that uses it changes)"
        items.append({"id": f"episode:{f}", "scope": "episode", "field": f, "value": v,
                      "dest": "series", "summary": summary, "_set": (block, key, v)})
    return items, left


def ref_candidates(st: State) -> tuple[list, list]:
    import h3refs as R
    items, left = [], []
    try:
        defaults = R.image_defaults(st.s)
    except R.RefError:
        defaults = None
    for home, data in st.ref_ov.items():
        for ref_id, block in (data.get("refs") or {}).items():
            if not isinstance(block, dict):
                continue
            try:
                ref = R.find_ref(st.s, ref_id)
            except (R.RefError, R.UnknownRef) as e:
                for f in _ref_fields(block):
                    left.append(_left("ref", f, f"{e}", ref=ref_id))
                continue
            if os.path.normcase(ref.home) != os.path.normcase(home):
                continue                                  # not where this ref's overrides live
            prompts = {}
            for f in _ref_fields(block):
                if f == "prompt":
                    continue
                left.append(_left("ref", f, REF_LEFT.get(f, "no series config field"),
                                  ref=ref_id))
            if block.get("prompt") is not None:
                prompts[None] = block["prompt"]
            for v, vb in (block.get("views") or {}).items():
                if isinstance(vb, dict) and vb.get("prompt") is not None:
                    prompts[v] = vb["prompt"]
            if not prompts:
                continue
            item, why = _design_item(st, ref, prompts, data, defaults)
            if item:
                items.append(item)
            else:
                left.append(_left("ref", "prompt", why, ref=ref_id,
                                  view=(next(iter(prompts)) if len(prompts) == 1 else None)))
    return items, left


def _ref_fields(block: dict) -> list[str]:
    import h3refs as R
    out = [f for f in R.OVERRIDE_FIELDS if block.get(f) is not None]
    for vb in (block.get("views") or {}).values():
        if isinstance(vb, dict):
            out += [f for f in R.OVERRIDE_FIELDS if vb.get(f) is not None]
    return list(dict.fromkeys(out))


def _design_item(st: State, ref, prompts: dict, data: dict,
                 defaults) -> tuple[dict | None, str]:
    """A ref prompt override as a new design / description sentence: the
    override must be the built prompt with only that sentence changed, the
    same change in every view, and a generate with the new sentence and no
    prompt override must give exactly the override's text."""
    import h3refs as R
    if ref.kind == "keyframe":
        return None, "a keyframe's prompt is written from its shot: no series config field"
    if ref.kind == "voice":
        return None, "a voice has no prompt in the series config"
    field = "description" if ref.kind == "location" else "design"
    book = "locations" if ref.kind == "location" else "subjects"
    key = ref.id.split(":", 1)[1]
    old = ref.entry.get(field)
    if not isinstance(old, str) or not old:
        return None, f"{ref.id} has no {field} in the series config to rewrite"
    if ref.has_views:
        missing = [v for v in R.VIEW_TAGS if v not in prompts]
        if missing:
            return None, (f"only {', '.join(v for v in prompts if v)} of {ref.id}'s views "
                          f"have a prompt override; the {field} describes all four views, "
                          f"so changing it would change {', '.join(missing)} too")
    elif None not in prompts:
        return None, f"{ref.id} has per-view prompts but no views"
    bare = copy.deepcopy(data)
    for vb in [bare["refs"][ref.id]] + list((bare["refs"][ref.id].get("views") or {}).values()):
        vb.pop("prompt", None)
        vb.pop("base_hash", None)
    new = None
    for v, text in prompts.items():
        eff = R.effective(st.s, ref, v, bare, defaults=defaults)
        if not eff or not isinstance(eff.get("prompt"), str) or not isinstance(text, str):
            return None, f"{ref.id} can't be generated, so its prompt can't be checked"
        base = eff["prompt"]
        if base.count(old) != 1:
            return None, (f"the built prompt of {ref.id}{' ' + v if v else ''} doesn't hold "
                          f"its {field} exactly once")
        a, b = base.split(old)
        if not (text.startswith(a) and text.endswith(b) and len(text) > len(a) + len(b)):
            return None, (f"the override changes more of the prompt than the {field} "
                          f"sentence: edit the series config by hand")
        mine = text[len(a):len(text) - len(b)]
        if new is not None and mine != new:
            return None, f"the views' overrides change the {field} in different ways"
        new = mine
    # check: the series config with the new sentence builds each prompt exactly
    cfg2 = copy.deepcopy(st.cfg)
    cfg2[book][key][field] = new
    s2 = R.Series(st.s.ep, st.s.config_file, st.s.home, cfg2)
    ref2 = R.find_ref(s2, ref.id)
    for v, text in prompts.items():
        eff = R.effective(s2, ref2, v, bare, defaults=defaults)
        if not eff or eff.get("prompt") != text:
            return None, (f"the series config can't reproduce the override's text "
                          f"with a new {field}")
    users = _users(st, ref)
    summary = f"{ref.id}: series config {book}.{key}.{field} → {new!r}"
    if new == old:
        summary = f"{ref.id}: the override is the built prompt; it is dropped"
    elif users:
        summary += (f"; the prompts of {len(users)} shot(s) that show it change too "
                    f"({', '.join(users[:6])}{' …' if len(users) > 6 else ''})")
    return ({"id": f"ref:{ref.id}:prompt", "scope": "ref", "ref": ref.id, "field": "prompt",
             "value": new, "dest": "series", "summary": summary,
             "_set": (book, key, field, new), "_views": list(prompts)}, "")


def _users(st: State, ref) -> list[str]:
    key = ref.id.split(":", 1)[1]
    out = []
    for sq in st.story.sequences:
        for sh in sq.shots:
            if (ref.kind == "location" and sh.plate == key) or \
                    (ref.kind != "location" and (key in sh.cast or key in sh.props)):
                out.append(sh.id)
    return out


def _apply(st: State, items: list[dict]) -> tuple[str, dict, dict]:
    """(new script text, new raw series config, new overrides.json) with
    `items` promoted (nothing written)."""
    edits: dict[str, list] = {}
    raw = copy.deepcopy(st.raw)
    ov = copy.deepcopy(st.ov)
    targets = effective_targets(st.story, st.cfg, st.ov)
    for it in items:
        if it["scope"] == "shot":
            sid = it["shot"]
            edits.setdefault(sid, []).append(it["_edit"])
            if it["field"] == "target":
                T.set_shot_target(ov, sid, None)
            else:
                for ps in T.PASSES:
                    if (((ov.get("shots") or {}).get(sid) or {}).get(targets[sid]) or {}):
                        T.set_override(ov, sid, ps, targets[sid], **{it["field"]: None})
        elif it["scope"] == "episode":
            block, key, v = it["_set"]
            if not isinstance(raw.get(block), dict):
                raw[block] = {}
            raw[block][key] = v
            T.set_episode_field(ov, it["field"], None)
        else:
            book, key, field, v = it["_set"]
            raw[book][key][field] = v
    order = ("target", "model", "lora", "steps")
    for sid in edits:
        edits[sid].sort(key=lambda kv: order.index(kv[0]))
    text = st.script.text
    if edits:
        spans = {sh.id: (sh.source["line"], sh.source["end_line"])
                 for sq in st.story.sequences for sh in sq.shots}
        text = edit_script(text, spans, edits)
    return text, raw, ov


def _verify(st: State, items: list[dict], before: dict) -> tuple[dict[str, str], str | None]:
    """({shot: why it would render differently}, why the promoted files don't
    build or read back, or None) for `items` promoted together: the promoted
    files compiled in memory, every shot and pass compared with `before`."""
    from h3core.series_config import series_config_from
    from h3core.story import ScriptError
    text, raw, ov = _apply(st, items)
    try:
        cfg = series_config_from(copy.deepcopy(raw))
        story = parse(text, cfg)
        after = render_values(story, cfg, ov)
    except (ScriptError, ValueError, TG.TargetError) as e:
        return {}, f"the promoted files don't build: {e}"
    bad: dict[str, str] = {}
    for key, was in before.items():
        now = after.get(key) or {}
        if now != was:
            sid, ps = key
            f = next((f for f in ("target", "model", "loras", "steps", "error")
                      if now.get(f) != was.get(f)), "target")
            bad.setdefault(sid, f"{sid} would render differently ({ps}: {f} "
                                f"{_show(was.get(f))} → {_show(now.get(f))})")
    # the promoted lines are the ones the parser now reads
    shots = {sh.id: sh for sq in story.sequences for sh in sq.shots}
    for it in items:
        if it["scope"] != "shot":
            continue
        sh, (key, want) = shots.get(it["shot"]), it["_edit"]
        o = (sh.overrides or {}) if sh else {}
        got = {"target": sh.target if sh else None, "model": o.get("model"),
               "lora": o.get("lora"),
               "steps": None if o.get("steps") is None else str(o["steps"])}[key]
        if got != want:
            return bad, f"the `{key}:` line didn't read back as {want!r}"
    return bad, None


def _check(st: State, items: list[dict], before: dict) -> tuple[list[dict], list[dict]]:
    """(items that promote faithfully, left entries for the rest). Each item
    is checked alone first, then the survivors together; an interaction
    drops the items of the shots it changes (else the episode items)."""
    left, keep = [], []
    for it in items:
        if it["scope"] == "ref":                         # checked when it was made
            keep.append(it)
            continue
        bad, broken = _verify(st, [it], before)
        why = broken or next(iter(bad.values()), None)
        if why:
            left.append(_left(it["scope"], it["field"], why, shot=it.get("shot")))
        else:
            keep.append(it)
    for _ in range(len(keep) + 1):
        bad, broken = _verify(st, keep, before)
        if not bad and not broken:
            break
        blamed = [it for it in keep if it.get("shot") in bad] or \
            [it for it in keep if it["scope"] == "episode"] or \
            [it for it in keep if it["scope"] != "ref"]
        if not blamed:
            break
        for it in blamed:
            why = bad.get(it.get("shot")) or broken or next(iter(bad.values()))
            left.append(_left(it["scope"], it["field"], why + " (with the other items)",
                              shot=it.get("shot")))
        keep = [it for it in keep if it not in blamed]
    return keep, left


def make_plan(ep: str, shot: str | None = None, only: set[str] | None = None,
              st: State | None = None) -> dict:
    """GET /h3pipe/promote: {"items", "left", "diffs", "hashes"}; with `only`,
    just those candidate ids are promoted. Private keys (a leading `_`) are
    for apply: `_candidates` (every id before the checks), `_texts` (the
    promoted files), items' `_edit` / `_set`; public() strips them."""
    st = st or State(ep)
    try:
        before = render_values(st.story, st.cfg, st.ov)
    except (ValueError, TG.TargetError) as e:
        raise PromoteError(400, f"the episode doesn't build ({e}): fix it before "
                                f"promoting") from None
    items, left = shot_candidates(st, before, shot)
    if not shot:
        ei, el = episode_candidates(st)
        ri, rl = ref_candidates(st)
        items, left = items + ei + ri, left + el + rl
    candidates = [it["id"] for it in items]
    if only is not None:
        items = [it for it in items if it["id"] in only]
    items, dropped = _check(st, items, before)
    left += dropped
    text, raw, _ = _apply(st, items)
    new_series = st.series.text
    if any(it["dest"] == "series" for it in items):
        new_series = dump_series(raw, st.series.text)
    series_diff = unified(st.series.text, new_series, os.path.basename(st.series.path))
    if series_diff and not formatted(st.series.text):
        series_diff = (f"# {os.path.basename(st.series.path)} wasn't formatted with a 2-space "
                       f"indent: promoting rewrites the whole file that way\n" + series_diff)
    if any(it["dest"] == "script" for it in items):
        story2 = parse(text, series_cfg_of(raw))
        spans = {sh.id: (sh.source["line"], sh.source["end_line"])
                 for sq in story2.sequences for sh in sq.shots}
        for it in items:
            if it["dest"] == "script" and it["shot"] in spans:
                it["line"] = key_line(text, spans[it["shot"]], it["_edit"][0])
    return {"items": items, "left": left,
            "diffs": {"script": unified(st.script.text, text,
                                        os.path.basename(st.script.path)),
                      "series": series_diff},
            "hashes": {"script": st.script.hash, "series": st.series.hash},
            "_candidates": candidates, "_texts": {"script": text, "series": new_series}}


def series_cfg_of(raw: dict) -> dict:
    from h3core.series_config import series_config_from
    return series_config_from(copy.deepcopy(raw))


def public(obj):
    """A plan (or item) without its private keys."""
    if isinstance(obj, dict):
        return {k: public(v) for k, v in obj.items() if not k.startswith("_")}
    if isinstance(obj, list):
        return [public(v) for v in obj]
    return obj


def plan(ep: str, shot: str | None = None) -> dict:
    return public(make_plan(ep, shot))


def apply(ep: str, ids, hashes: dict | None, shot: str | None = None,
          build: bool = True) -> dict:
    """POST /h3pipe/promote: promote the plan items `ids` (a list of ids, or
    "all"; `shot` narrows the plan as GET does) if both files still hash to
    `hashes` (else Conflict, 409). Writes the files (with _history/ copies),
    drops exactly the promoted override fields, rebuilds. Returns
    {"promoted", "left", "hashes", "build", "refs" (the refs whose overrides
    changed)}. None for `hashes` skips the check (the CLI's plan is fresh)."""
    import h3refs as R
    st = State(ep)
    if hashes is not None:
        if not isinstance(hashes, dict):
            raise PromoteError(400, "hashes must be {\"script\": …, \"series\": …} from the plan")
        for f, src in (("script", st.script), ("series", st.series)):
            if hashes.get(f) != src.hash:
                raise H.Conflict(src)
    if ids == "all":
        only = None
    elif isinstance(ids, list) and all(isinstance(i, str) for i in ids):
        only = set(ids)
    else:
        raise PromoteError(400, "items must be a list of item ids, or \"all\"")
    p = make_plan(ep, shot, only=only, st=st)
    unknown = sorted((only or set()) - set(p["_candidates"]))
    if unknown:
        raise PromoteError(400, f"no such item in the plan: {', '.join(unknown)} "
                                f"(ask for the plan again)")
    items = p["items"]
    new_hashes = {"script": st.script.hash, "series": st.series.hash}
    if items:
        new_hashes["script"] = H.write_source(st.script, p["_texts"]["script"])
        new_hashes["series"] = H.write_source(st.series, p["_texts"]["series"])
        # overrides: exactly the promoted fields, from the files as they are now
        ov = T.load_overrides(st.ep)
        targets = effective_targets(st.story, st.cfg, st.ov)
        changed = False
        for it in items:
            if it["scope"] == "shot":
                sid = it["shot"]
                if it["field"] == "target":
                    T.set_shot_target(ov, sid, None)
                elif ((ov.get("shots") or {}).get(sid) or {}).get(targets[sid]):
                    for ps in T.PASSES:
                        T.set_override(ov, sid, ps, targets[sid], **{it["field"]: None})
                changed = True
            elif it["scope"] == "episode":
                T.set_episode_field(ov, it["field"], None)
                changed = True
        if changed:
            T.save_overrides(st.ep, ov)
        for it in items:
            if it["scope"] != "ref":
                continue
            ref = R.find_ref(st.s, it["ref"])
            data = R.load_overrides(ref.home)
            for v in it["_views"]:
                R.set_ref_override(data, ref, v, {"prompt": None}, None)
            R.save_overrides(ref.home, data)
    result = {"promoted": [it["id"] for it in items], "left": public(p["left"]),
              "hashes": new_hashes, "build": None,
              "refs": sorted({it["ref"] for it in items if it["scope"] == "ref"})}
    if items and build:
        result["build"] = E.build_episode(st.ep)
    return result


# ---------------------------------------------------------------------------
# the command
# ---------------------------------------------------------------------------

def cmd_promote(root: str, argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="h3.py promote",
                                 description="Move overrides into the script and the "
                                             "series config, then drop them.")
    ap.add_argument("shot", nargs="?", help="only this shot's overrides")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--all", action="store_true", help="promote everything the plan offers")
    g.add_argument("--item", action="append", metavar="ID",
                   help="promote this item (repeat); the plan lists the ids")
    ap.add_argument("--dry-run", action="store_true", help="show the plan, write nothing")
    args = ap.parse_args(argv)
    only = set(args.item) if args.item else None
    try:
        p = make_plan(root, args.shot, only=only)
    except H.SourceError as e:
        print(f"  !! {e}")
        return 1
    unknown = sorted((only or set()) - set(p["_candidates"]))
    if unknown:
        print(f"  !! not in the plan: {', '.join(unknown)}")
        return 2
    items = p["items"]
    print(f"\n  promote: {len(items)} item(s)"
          + (f", {len(p['left'])} left in the overrides" if p["left"] else ""))
    for it in items:
        print(f"    {it['id']:<36} -> {it['dest']:<6} {it['summary']}")
    for x in p["left"]:
        where = x.get("shot") or x.get("ref") or x["scope"]
        print(f"    left  {where} {x['field']}" + (f" [{x['pass']}]" if x.get("pass") else "")
              + f": {x['reason']}")
    for name in ("script", "series"):
        if p["diffs"][name]:
            print("\n" + p["diffs"][name].rstrip("\n"))
    if not (args.all or args.item) or args.dry_run:
        if items and not args.dry_run:
            print("\n  nothing written: --all, or --item ID, promotes")
        print()
        return 0
    if not items:
        print("\n  nothing to promote\n")
        return 0
    try:
        r = apply(root, [it["id"] for it in items], p["hashes"], args.shot)
    except H.SourceError as e:
        print(f"  !! {e}")
        return 1
    print(f"\n  promoted {len(r['promoted'])}: {', '.join(r['promoted'])}")
    b = r.get("build") or {}
    if b and not b.get("ok"):
        print("  !! the rebuild failed:")
        for ps, v in (b.get("passes") or {}).items():
            if v.get("error"):
                print(f"     [{ps}] {v['error']}")
        return 1
    print("  rebuilt\n")
    return 0
