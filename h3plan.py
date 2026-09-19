#!/usr/bin/env python3
"""
h3plan.py -- Episode -> MiniMax H3 chain plan compiler.

Takes one episode file (script + cast + dialogue-track timings) and emits
the plan_json payloads for MiniMaxH3ChainPlan, one per render unit, plus a
scheduled-reference table, a shot manifest, and a timing report.

Design rules baked in:
  * Audio-first. Shot durations are derived from the dialogue track when
    audio_in/audio_out are present; explicit `duration` is the fallback.
  * Chain inside a beat, cut between beats. A sequence marked continuous=true
    compiles to ONE chained plan. continuous=false compiles to one independent
    single-shot plan per shot, which costs zero overlap frames and can render
    in any order, in parallel, and be re-rendered alone.
  * Every length lands on H3's 17k+5 frame grid, rounded up.
  * Seeds are deterministic from ids, so recompiling does not invalidate
    checkpoints for shots you did not touch.

Usage:
    python3 h3plan.py episode.json -o build/
    python3 h3plan.py episode.json -o build/ --proxy
    python3 h3plan.py episode.json --report-only
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# H3 frame grid
# ---------------------------------------------------------------------------

GRID_STEP = 17
GRID_BASE = 5
GRID_MAX = 3592  # 17*211 + 5


def snap_up(frames: int) -> int:
    """Round a raw frame count up onto H3's 17k+5 grid."""
    if frames <= GRID_BASE:
        return GRID_BASE
    k = math.ceil((frames - GRID_BASE) / GRID_STEP)
    value = GRID_STEP * k + GRID_BASE
    if value > GRID_MAX:
        raise ValueError(
            f"{frames} frames exceeds H3's maximum clip length of {GRID_MAX} "
            f"({GRID_MAX / 24:.2f}s at 24fps). Split this shot."
        )
    return value


def grid_neighbours(frames: int, fps: float) -> str:
    """Human-readable nearest grid options, for the report."""
    lo = snap_up(frames)
    hi = lo + GRID_STEP if lo + GRID_STEP <= GRID_MAX else lo
    return f"{lo} ({lo / fps:.2f}s) / {hi} ({hi / fps:.2f}s)"


# ---------------------------------------------------------------------------
# Deterministic seeds
# ---------------------------------------------------------------------------

def stable_seed(*parts: str) -> int:
    """uint64 seed derived from ids. Stable across runs and machines."""
    h = hashlib.sha256("::".join(parts).encode("utf-8")).digest()
    return int.from_bytes(h[:8], "big") & 0x7FFF_FFFF_FFFF_FFFF


def fingerprint(payload: Any) -> str:
    """Short content hash used for generation_fingerprint / change detection."""
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

@dataclass
class Shot:
    id: str
    seq_id: str
    index_in_seq: int
    raw_frames: int
    requested_frames: int
    duration_s: float
    cast: list[str]
    prompt: str
    seed: int
    audio_in: float | None = None
    audio_out: float | None = None
    pad_frames: int = 0
    ref_kinds: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class RenderUnit:
    """One queueable plan: a chained sequence, or a single cut shot."""
    id: str
    kind: str                       # "chain" | "single"
    seq_id: str
    shots: list[Shot]
    context_length: int
    plan: dict
    delivered_frames: int
    raw_frames: int
    refs: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Prompt construction (per h3-prompt-writing: Ref2VA six-section format)
# ---------------------------------------------------------------------------

def _subject_map(cast_ids: list[str], cast: dict) -> dict[str, str]:
    """Assign <Subject N> labels in stable order of first appearance."""
    return {cid: f"<Subject {i + 1}>" for i, cid in enumerate(cast_ids)}


# Reference-slot budget. H3 degrades past roughly four active picture
# references in one scene, and every character costs a slot. A two-hander
# using face+look per character would spend the whole budget on two people,
# so slot allocation is driven by shot size:
#   close   -> the face sheet carries identity; that is what the shot is for
#   med/wide-> the turnaround carries silhouette, wardrobe and proportion
# A single-character close-up is the one case that can afford both.
REF_POLICY = {
    "close": ["face", "look"],
    "medium": ["look"],
    "wide": ["look"],
}


def refs_for_shot(shot_src: dict, cast_ids: list[str]) -> dict[str, list[str]]:
    """Decide which reference kinds each character contributes in this shot."""
    size = (shot_src.get("size") or "medium").lower()
    kinds = REF_POLICY.get(size, ["look"])
    if len(cast_ids) > 1:
        kinds = ["look"]          # multi-character shots get one slot each
    override = shot_src.get("refs")
    if override:
        kinds = list(override)
    return {cid: kinds for cid in cast_ids}


def _speaker_map(shot_src: dict, cast_ids: list[str]) -> dict[str, str]:
    """Assign (Sx) IDs by order of actual vocal events, per the guide."""
    order: list[str] = []
    for line in shot_src.get("dialogue", []) or []:
        who = line.get("who")
        if who and who not in order:
            order.append(who)
    return {cid: f"(S{i + 1})" for i, cid in enumerate(order)}


def build_prompt(
    shot_src: dict,
    seq: dict,
    ep: dict,
    cast: dict,
    style: dict,
    is_continuation: bool,
    audio_mode: str,
) -> str:
    """Emit a Ref2VA six-section prompt, or a plain three-field prompt when the
    shot declares no cast references."""
    cast_ids = shot_src.get("cast", []) or []
    subj = _subject_map(cast_ids, cast)
    spk = _speaker_map(shot_src, cast_ids)

    style_line = style.get("prefix", "").strip()
    action = shot_src.get("action", "").strip()
    camera = shot_src.get("camera", "").strip()
    sound = shot_src.get("sound", "").strip()
    music = (shot_src.get("music") or "N/A").strip()
    env = seq.get("location", "").strip()

    # ---- no references: three-field base format --------------------------
    if not cast_ids:
        body = f"integrated_multimodal_description: [Shot 1] {style_line} {env} {action}".strip()
        body += f" {camera}" if camera else " The camera holds a static shot with fixed composition."
        if is_continuation:
            body += (
                " Continue the incoming action, pose, camera movement, lighting, and "
                "object states from the previous segment without resetting them."
            )
        return "\n\n".join([
            body,
            f"overall_soundscape: {sound or 'Continuous environmental ambience appropriate to the scene, with synchronized physical action sounds.'}",
            f"non_diegetic_music: {music}",
        ])

    # ---- subject_definitions --------------------------------------------
    active = refs_for_shot(shot_src, cast_ids)
    defs: list[str] = ["subject_definitions:"]
    for cid in cast_ids:
        c = cast.get(cid, {})
        kinds = active.get(cid, [])
        src_bits = []
        if "face" in kinds and c.get("face_tag"):
            src_bits.append(f"@{c['face_tag']} for facial identity")
        if "look" in kinds and c.get("look_tag"):
            src_bits.append(
                f"@{c['look_tag']} for appearance, wardrobe, proportions, and facial identity"
                if "face" not in kinds else
                f"@{c['look_tag']} for full-body appearance, wardrobe, and proportions"
            )
        src = " and ".join(src_bits) if src_bits else "the established character design"
        defs.append(
            f"{subj[cid]} is {c.get('name', cid)}, defined by {src}. "
            f"Preserve the exact reference styling: {c.get('design', '').strip()}"
        )
    if audio_mode == "source_track":
        defs.append(
            "@song is the current frame-exact source-track slice and provides the "
            "English dialogue content, vocal delivery, timing, and phrasing for this segment."
        )

    # ---- summary ---------------------------------------------------------
    task_types = ["reference generation"]
    if audio_mode == "source_track":
        task_types.append("audio reference")
    beat = shot_src.get("beat", action)[:240]
    summary = (
        f"summary:\n[{' + '.join(task_types)}] "
        f"{beat}"
    )

    # ---- retention_analysis ---------------------------------------------
    ret: list[str] = ["retention_analysis:"]
    for cid in cast_ids:
        ret.append(
            f"{subj[cid]} (appears in [Shot 1]): fully_preserved - preserve facial identity, "
            f"hairstyle, body proportions, wardrobe, colors, and distinctive features from the "
            f"reference while allowing natural poses and expressions."
        )
    if audio_mode == "source_track":
        ret.append(
            "@song: reference - its dialogue content, vocal delivery, timing, and phrasing "
            "guide the performance without copying the source signal."
        )

    # ---- detailed_description -------------------------------------------
    desc: list[str] = ["detailed_description:"]
    desc.append(f"The target video is {style_line}")
    line1 = f"[Shot 1] {env} {action}".strip()
    if camera:
        line1 += f" {camera}"
    else:
        line1 += " The camera holds a static shot with fixed composition."
    if is_continuation:
        line1 += (
            " Continue the incoming action, pose, camera movement, lighting, and object "
            "states from the previous segment without resetting them."
        )
    desc.append(line1)

    for line in shot_src.get("dialogue", []) or []:
        who = line.get("who")
        text = (line.get("line") or "").strip()
        lang = line.get("lang", "English")
        delivery = line.get("delivery", "").strip()
        if not who or not text:
            continue
        label = f"{subj.get(who, '')} {spk.get(who, '')}".strip()
        deliv = f" {delivery}" if delivery else ""
        if line.get("offscreen"):
            desc.append(
                f"{label} says in an off-screen voiceover:{deliv} <d>[{lang}] {text}</d> "
                f"while the on-screen character's lips remain completely closed."
            )
        else:
            desc.append(f"{label} says,{deliv} <d>[{lang}] {text}</d>")

    if shot_src.get("screen_text"):
        desc.append(f'Visible on-screen text reads "{shot_src["screen_text"]}".')

    return "\n\n".join([
        "\n".join(defs),
        summary,
        "\n".join(ret),
        "\n".join(desc),
        f"overall_soundscape: {sound or 'Continuous room tone and environmental ambience with synchronized physical action sounds.'}",
        f"non_diegetic_music: {music}",
    ])


# ---------------------------------------------------------------------------
# Compiler
# ---------------------------------------------------------------------------

class Compiler:
    def __init__(self, ep: dict, proxy: bool = False):
        self.ep = ep
        self.proxy = proxy
        self.meta = ep.get("episode", {})
        self.style = ep.get("style", {})
        # `_`-prefixed keys are authoring comments, not data.
        self.cast = {
            k: v for k, v in ep.get("cast", {}).items()
            if not k.startswith("_") and isinstance(v, dict)
        }
        self.audio = ep.get("audio", {})
        self.fps = float(self.meta.get("fps", 24))
        self.warnings: list[str] = []

        if proxy:
            p = ep.get("proxy", {})
            self.width = int(p.get("width", 480))
            self.height = int(p.get("height", 272))
            self.steps = int(p.get("steps", 3))
        else:
            self.width = int(self.meta.get("width", 960))
            self.height = int(self.meta.get("height", 544))
            self.steps = int(self.meta.get("steps", 5))

        self.audio_mode = self.audio.get("mode", "source_track")
        self.run_name = self.meta.get("id", "episode")
        if proxy:
            self.run_name += "_proxy"

    # -- timing ------------------------------------------------------------

    def shot_frames(self, s: dict) -> tuple[int, int, float]:
        """Return (raw_frames_on_grid, requested_frames, requested_seconds)."""
        if "audio_in" in s and "audio_out" in s:
            dur = float(s["audio_out"]) - float(s["audio_in"])
            if dur <= 0:
                raise ValueError(f"shot {s['id']}: audio_out must be after audio_in")
        elif "duration" in s:
            dur = float(s["duration"])
        else:
            raise ValueError(
                f"shot {s['id']}: needs either audio_in/audio_out or duration"
            )
        requested = max(1, round(dur * self.fps))
        return snap_up(requested), requested, dur

    # -- build -------------------------------------------------------------

    def compile(self) -> tuple[list[RenderUnit], dict]:
        units: list[RenderUnit] = []
        all_shots: list[Shot] = []

        for seq in self.ep.get("sequences", []):
            seq_id = seq["id"]
            continuous = bool(seq.get("continuous", False))
            ctx = int(seq.get("context_length", self.ep.get("defaults", {}).get("context_length", 22)))
            if not continuous:
                ctx = 0

            shots: list[Shot] = []
            for i, s in enumerate(seq.get("shots", [])):
                raw, requested, dur = self.shot_frames(s)
                pad = raw - requested
                shot = Shot(
                    id=s["id"],
                    seq_id=seq_id,
                    index_in_seq=i,
                    raw_frames=raw,
                    requested_frames=requested,
                    duration_s=dur,
                    cast=s.get("cast", []) or [],
                    prompt=build_prompt(
                        s, seq, self.ep, self.cast, self.style,
                        is_continuation=(continuous and i > 0),
                        audio_mode=self.audio_mode,
                    ),
                    seed=stable_seed(self.meta.get("id", "ep"), seq_id, s["id"]),
                    audio_in=s.get("audio_in"),
                    audio_out=s.get("audio_out"),
                    pad_frames=pad,
                    ref_kinds=refs_for_shot(s, s.get("cast", []) or []),
                )
                shots.append(shot)
                all_shots.append(shot)

                # overlap tax warning
                if continuous and i > 0 and ctx > 0:
                    tax = ctx / raw
                    if tax > 0.15:
                        self.warnings.append(
                            f"{seq_id}/{shot.id}: context {ctx} on a {raw}-frame clip wastes "
                            f"{tax:.0%} of the render. Lower context_length or lengthen the shot."
                        )
                # Only actionable when the duration was chosen rather than read
                # off the dialogue track -- audio-derived pad is unavoidable and
                # gets trimmed at conform.
                author_chosen = "audio_in" not in s
                if author_chosen and pad / raw > 0.15:
                    self.warnings.append(
                        f"{seq_id}/{shot.id}: {dur:.2f}s snaps up to {raw} frames, "
                        f"wasting {pad} frames ({pad / self.fps:.2f}s). "
                        f"Writing this shot at {raw / self.fps:.2f}s costs the same render."
                    )

            if not shots:
                continue

            if continuous:
                units.append(self._chain_unit(seq, shots, ctx))
            else:
                for shot in shots:
                    units.append(self._single_unit(seq, shot))

        report = self._report(units, all_shots)
        return units, report

    def _plan_widgets(self, plan_json: str, run_name: str, ctx: int) -> list:
        """The MiniMaxH3ChainPlan widgets_values array, in node order."""
        return [
            plan_json,
            run_name,
            fingerprint({
                "w": self.width, "h": self.height, "steps": self.steps,
                "mode": self.audio_mode, "ctx": ctx,
                "style": self.style.get("prefix", ""),
            }),
            self.width,
            self.height,
            # single-shot units never chain, so context is inert; 5 is the
            # smallest legal COMBO value.
            ctx if ctx else 5,
            "video",
            "head",
            "disabled",
            self.audio_mode,
            # source_track pulls a frame-exact slice per clip, so audio context
            # must be 0 or the slices overlap. Generated audio wants it to match
            # the video context for continuity across the join.
            0 if self.audio_mode == "source_track" else (ctx if ctx else 5),
            10,     # default_duration_seconds (every shot sets `length` explicitly)
            self.steps,
            0,      # base_seed unused; every shot carries an explicit seed
            20,     # segment_crf
        ]

    def _shot_entry(self, shot: Shot) -> dict:
        return {
            "id": shot.id,
            "prompt": shot.prompt.split("\n\n"),
            "length": shot.raw_frames,
            "steps": self.steps,
            "seed": str(shot.seed),
        }

    def _chain_unit(self, seq: dict, shots: list[Shot], ctx: int) -> RenderUnit:
        plan_obj = {
            "prompt_prefix": self.style.get("prompt_prefix", ""),
            "defaults": {"steps": self.steps},
            "shots": [self._shot_entry(s) for s in shots],
        }
        plan_json = json.dumps(plan_obj, ensure_ascii=False, indent=2)
        raw = sum(s.raw_frames for s in shots)
        delivered = shots[0].raw_frames + sum(s.raw_frames - ctx for s in shots[1:])
        return RenderUnit(
            id=f"{self.run_name}_{seq['id']}",
            kind="chain",
            seq_id=seq["id"],
            shots=shots,
            context_length=ctx,
            plan={
                "plan_json": plan_obj,
                "widgets_values": self._plan_widgets(
                    plan_json, f"{self.run_name}_{seq['id']}", ctx
                ),
            },
            delivered_frames=delivered,
            raw_frames=raw,
            refs=self._refs_for(shots, len(shots)),
        )

    def _single_unit(self, seq: dict, shot: Shot) -> RenderUnit:
        plan_obj = {
            "prompt_prefix": self.style.get("prompt_prefix", ""),
            "defaults": {"steps": self.steps},
            "shots": [self._shot_entry(shot)],
        }
        plan_json = json.dumps(plan_obj, ensure_ascii=False, indent=2)
        return RenderUnit(
            id=f"{self.run_name}_{seq['id']}_{shot.id}",
            kind="single",
            seq_id=seq["id"],
            shots=[shot],
            context_length=0,
            plan={
                "plan_json": plan_obj,
                "widgets_values": self._plan_widgets(
                    plan_json, f"{self.run_name}_{seq['id']}_{shot.id}", 0
                ),
            },
            delivered_frames=shot.raw_frames,
            raw_frames=shot.raw_frames,
            refs=self._refs_for([shot], 1),
        )

    def _refs_for(self, shots: list[Shot], n_scenes: int) -> list[dict]:
        """Scheduled @tag reference table for this unit.

        Emits one entry per active reference with the scene range (1-indexed,
        inclusive, in the node's "a:b" form) over which it is active.
        """
        spans: dict[str, list[int]] = {}
        images: dict[str, str | None] = {}
        for i, shot in enumerate(shots, start=1):
            for cid in shot.cast:
                c = self.cast.get(cid, {})
                # only the kinds this shot actually spends a slot on
                for kind in shot.ref_kinds.get(cid, []):
                    tag = c.get(f"{kind}_tag")
                    if not tag:
                        continue
                    spans.setdefault(tag, []).append(i)
                    images[tag] = c.get(f"{kind}_ref")

        refs: list[dict] = []
        for tag, scenes in spans.items():
            lo, hi = min(scenes), max(scenes)
            rng = "all" if (lo == 1 and hi == n_scenes) else f"{lo}:{hi}"
            refs.append({
                "type": "picture",
                "tag": tag,
                "scene_range": rng,
                "image": images.get(tag),
                "active_scenes": sorted(set(scenes)),
            })

        refs.sort(key=lambda r: (r["active_scenes"][0], r["tag"]))

        if self.audio_mode == "source_track":
            refs.append({
                "type": "audio", "tag": "song", "scene_range": "all",
                "image": self.audio.get("track"),
                "active_scenes": list(range(1, n_scenes + 1)),
            })

        max_pics = max(
            (sum(1 for r in refs if r["type"] == "picture" and i in r["active_scenes"])
             for i in range(1, n_scenes + 1)),
            default=0,
        )
        if max_pics > 4:
            self.warnings.append(
                f"{shots[0].seq_id}: {max_pics} picture references active in one scene. "
                f"H3 degrades past ~4 - split the shot or drop to one ref per character."
            )
        return refs

    # -- report ------------------------------------------------------------

    def _report(self, units: list[RenderUnit], shots: list[Shot]) -> dict:
        delivered = sum(u.delivered_frames for u in units)
        raw = sum(u.raw_frames for u in units)
        requested = sum(s.requested_frames for s in shots)
        chains = [u for u in units if u.kind == "chain"]
        singles = [u for u in units if u.kind == "single"]
        return {
            "episode": self.meta.get("id"),
            "mode": "proxy" if self.proxy else "final",
            "resolution": f"{self.width}x{self.height}",
            "steps": self.steps,
            "audio_mode": self.audio_mode,
            "fps": self.fps,
            "shots": len(shots),
            "render_units": len(units),
            "chained_units": len(chains),
            "independent_units": len(singles),
            "parallelisable_units": len(singles),
            "frames_requested": requested,
            "frames_raw": raw,
            "frames_delivered": delivered,
            "runtime_delivered_s": round(delivered / self.fps, 2),
            "runtime_delivered_mmss": _mmss(delivered / self.fps),
            "runtime_target_s": round(requested / self.fps, 2),
            "grid_pad_frames": raw - requested,
            "overlap_frames": raw - delivered,
            "waste_pct": round(100 * (raw - delivered + (raw - requested)) / raw, 2) if raw else 0,
            "warnings": self.warnings,
        }


def _mmss(seconds: float) -> str:
    m, s = divmod(seconds, 60)
    return f"{int(m):d}:{s:05.2f}"


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def write_build(units: list[RenderUnit], report: dict, outdir: str) -> None:
    os.makedirs(outdir, exist_ok=True)
    plans_dir = os.path.join(outdir, "plans")
    os.makedirs(plans_dir, exist_ok=True)

    manifest = {"report": report, "units": []}
    for u in units:
        path = os.path.join(plans_dir, f"{u.id}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({
                "unit_id": u.id,
                "kind": u.kind,
                "sequence": u.seq_id,
                "context_length": u.context_length,
                "paste_into_MiniMaxH3ChainPlan": json.dumps(
                    u.plan["plan_json"], ensure_ascii=False, indent=2
                ),
                "widgets_values": u.plan["widgets_values"],
                "scheduled_references": u.refs,
                "shots": [
                    {
                        "id": s.id, "raw_frames": s.raw_frames,
                        "requested_frames": s.requested_frames,
                        "pad_frames": s.pad_frames, "seed": s.seed,
                        "audio_in": s.audio_in, "audio_out": s.audio_out,
                        "cast": s.cast,
                    } for s in u.shots
                ],
            }, fh, ensure_ascii=False, indent=2)

        manifest["units"].append({
            "unit_id": u.id, "kind": u.kind, "sequence": u.seq_id,
            "plan_file": os.path.relpath(path, outdir),
            "delivered_frames": u.delivered_frames,
            "shots": [s.id for s in u.shots],
            "references": [r["tag"] for r in u.refs],
        })

    with open(os.path.join(outdir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)


def print_report(report: dict) -> None:
    r = report
    print(f"\n  episode {r['episode']}  [{r['mode']}]  {r['resolution']}  "
          f"{r['steps']} steps  audio={r['audio_mode']}")
    print(f"  {'-' * 62}")
    print(f"  shots                {r['shots']}")
    print(f"  render units         {r['render_units']}  "
          f"({r['chained_units']} chained, {r['independent_units']} independent)")
    print(f"  parallelisable       {r['parallelisable_units']} units can render in any order")
    print(f"  {'-' * 62}")
    print(f"  target runtime       {_mmss(r['runtime_target_s'])}")
    print(f"  delivered runtime    {r['runtime_delivered_mmss']}")
    print(f"  raw frames           {r['frames_raw']:,}")
    print(f"  delivered frames     {r['frames_delivered']:,}")
    print(f"  grid pad             {r['grid_pad_frames']:,} frames (trim in the NLE)")
    print(f"  overlap discarded    {r['overlap_frames']:,} frames")
    print(f"  total waste          {r['waste_pct']}% of raw render")
    if r["warnings"]:
        print(f"\n  warnings ({len(r['warnings'])}):")
        for w in r["warnings"]:
            print(f"    ! {w}")
    print()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("episode", help="episode JSON file")
    ap.add_argument("-o", "--out", default="build", help="output directory")
    ap.add_argument("--proxy", action="store_true",
                    help="emit the low-res animatic pass (same seeds)")
    ap.add_argument("--report-only", action="store_true",
                    help="print timing report without writing plans")
    args = ap.parse_args()

    with open(args.episode, encoding="utf-8") as fh:
        ep = json.load(fh)

    try:
        units, report = Compiler(ep, proxy=args.proxy).compile()
    except (ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print_report(report)
    if not args.report_only:
        write_build(units, report, args.out)
        print(f"  wrote {len(units)} plan files -> {args.out}/plans/")
        print(f"  manifest -> {args.out}/manifest.json\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
