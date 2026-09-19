// Phase 9b in the mock: cut.json as h3takes keeps it (resolve_cut,
// cut_entry_to_json), the PUT /h3pipe/cut checks, and /cut/reset and /cut/copy.
// Pure; mockApi.ts holds the state.

import { outOfOrder } from "../lib/cutEdit";
import { trimWindow } from "../lib/playlist";
import type { CutEntry, CutWhat, Pass, ShotStatus } from "../types";

export const ENTRY_FIELDS = ["shot", "take", "pass", "trim_in", "trim_out", "locked", "note"];

export interface Resolved {
  shot: string;
  /** the pass its take comes from */
  pass: Pass;
  take: number | null;
  trim_in: number;
  trim_out: number;
  locked: boolean;
  note: string;
  in_cut_file: boolean;
  orphan: boolean;
  placeholder: boolean;
}

/** h3takes.resolve_cut: listed entries keep their order, unlisted script shots
 * go after their nearest earlier script shot, listed shots gone from the script are orphans. */
export function resolveCut(list: CutEntry[], pass: Pass, script: string[]): Resolved[] {
  const inScript = new Set(script);
  const out: Resolved[] = [];
  const seen = new Set<string>();
  for (const raw of list) {
    const sid = raw.shot;
    if (!sid || seen.has(sid)) continue;
    seen.add(sid);
    const src = raw.pass || pass;
    out.push({
      shot: sid, pass: src, take: raw.take ?? null, trim_in: Math.floor(raw.trim_in || 0), trim_out: Math.floor(raw.trim_out || 0),
      locked: !!raw.locked, note: raw.note || "", in_cut_file: true, orphan: !inScript.has(sid), placeholder: src !== pass,
    });
  }
  script.forEach((sid, i) => {
    if (seen.has(sid)) return;
    let pos = 0;
    for (let j = i - 1; j >= 0; j--) {
      const idx = out.findIndex((e) => e.shot === script[j]);
      if (idx >= 0) {
        pos = idx + 1;
        break;
      }
    }
    out.splice(pos, 0, { shot: sid, pass, take: null, trim_in: 0, trim_out: 0, locked: false, note: "", in_cut_file: false, orphan: false, placeholder: false });
    seen.add(sid);
  });
  return out;
}

/** h3takes.cut_entry_to_json */
export function toJson(e: Resolved, listPass: Pass): CutEntry {
  const out: CutEntry = { shot: e.shot };
  if (e.pass !== listPass) out.pass = e.pass;
  if (e.take != null) out.take = e.take;
  if (e.trim_in) out.trim_in = e.trim_in;
  if (e.trim_out) out.trim_out = e.trim_out;
  if (e.locked) out.locked = true;
  if (e.note) out.note = e.note;
  return out;
}

/** The whole resolved list, as the file would hold it once anything is written. */
export function materialize(list: CutEntry[], pass: Pass, script: string[]): CutEntry[] {
  return resolveCut(list, pass, script).map((e) => toJson(e, pass));
}

export class CutError extends Error {
  constructor(message: string, public status: number) {
    super(message);
  }
}

const isInt = (v: unknown) => typeof v === "number" && Number.isInteger(v);

/**
 * PUT /h3pipe/cut's checks: duplicate shots and unknown fields, negative or
 * non-integer trims, and trims that leave less than a frame of a take whose
 * frame count is known (`framesOf`: [frames, their fps] or null).
 */
export function checkEntries(entries: unknown, fps: number, framesOf: (e: CutEntry) => [number, number] | null): CutEntry[] {
  if (!Array.isArray(entries)) throw new CutError("entries must be a list", 400);
  const seen = new Set<string>();
  for (const e of entries as CutEntry[]) {
    if (!e || typeof e !== "object" || typeof e.shot !== "string" || !e.shot) throw new CutError("every entry needs a shot", 400);
    const bad = Object.keys(e).filter((k) => !ENTRY_FIELDS.includes(k));
    if (bad.length) throw new CutError(`${e.shot}: unknown field ${bad.join(", ")}`, 400);
    if (seen.has(e.shot)) throw new CutError(`${e.shot} is in the cut twice`, 400);
    seen.add(e.shot);
    for (const k of ["trim_in", "trim_out"] as const) {
      const v = e[k];
      if (v != null && (!isInt(v) || (v as number) < 0)) throw new CutError(`${e.shot}: ${k} must be a whole number of frames, 0 or more (got ${String(v)})`, 400);
    }
    if (e.take != null && !isInt(e.take)) throw new CutError(`${e.shot}: take must be a number`, 400);
    if (e.pass != null && e.pass !== "final" && e.pass !== "proxy") throw new CutError(`${e.shot}: unknown pass ${String(e.pass)}`, 400);
    const known = framesOf(e);
    if (known && (e.trim_in || e.trim_out)) {
      const [frames, rate] = known;
      if (!trimWindow(frames, e.trim_in ?? 0, e.trim_out ?? 0, fps, rate)) {
        throw new CutError(`${e.shot}: trim_in ${e.trim_in ?? 0} + trim_out ${e.trim_out ?? 0} leave less than one frame of its ${frames}`, 400);
      }
    }
  }
  return entries as CutEntry[];
}

/** POST /h3pipe/cut/reset: script order (orphans after) and/or zero trims; picks, locks and notes kept. */
export function resetEntries(list: CutEntry[], pass: Pass, script: string[], what: CutWhat): CutEntry[] {
  let out = materialize(list, pass, script);
  if (what !== "trims") {
    const pos = new Map(script.map((s, i) => [s, i]));
    const inScript = out.filter((e) => pos.has(e.shot)).sort((a, b) => pos.get(a.shot)! - pos.get(b.shot)!);
    out = [...inScript, ...out.filter((e) => !pos.has(e.shot))];
  }
  if (what !== "order") out = out.map(({ trim_in: _a, trim_out: _b, ...rest }) => rest);
  return out;
}

/** POST /h3pipe/cut/copy: the other pass's order and/or trims (converted by frame rate); never picks. */
export function copyEntries(
  from: CutEntry[], fromPass: Pass, fromFps: number, to: CutEntry[], toPass: Pass, toFps: number, script: string[], what: CutWhat,
): CutEntry[] {
  const src = materialize(from, fromPass, script);
  let out = materialize(to, toPass, script);
  if (what !== "trims") {
    const pos = new Map(src.map((e, i) => [e.shot, i]));
    const known = out.filter((e) => pos.has(e.shot)).sort((a, b) => pos.get(a.shot)! - pos.get(b.shot)!);
    out = [...known, ...out.filter((e) => !pos.has(e.shot))];
  }
  if (what !== "order") {
    const k = fromFps > 0 && toFps > 0 ? toFps / fromFps : 1;
    const by = new Map(src.map((e) => [e.shot, e]));
    out = out.map((e) => {
      const s = by.get(e.shot);
      const { trim_in: _a, trim_out: _b, ...rest } = e;
      const a = Math.round((s?.trim_in ?? 0) * k);
      const b = Math.round((s?.trim_out ?? 0) * k);
      return { ...rest, ...(a ? { trim_in: a } : {}), ...(b ? { trim_out: b } : {}) };
    });
  }
  return out;
}

/** Orders the pass's statuses as the resolved cut and fills each `cut` (h3edit.episode_status). */
export function applyCut(
  shots: ShotStatus[], list: CutEntry[], pass: Pass, script: string[], otherTakes: (shot: string) => ShotStatus["takes"],
): ShotStatus[] {
  const by = new Map(shots.map((s) => [s.shot, s]));
  const resolved = resolveCut(list, pass, script).filter((e) => by.has(e.shot));
  const sIdx = new Map(script.map((s, i) => [s, i]));
  const ooo = outOfOrder(resolved.map((e) => (sIdx.has(e.shot) ? sIdx.get(e.shot)! : null)));
  return resolved.map((e, i) => {
    const s = by.get(e.shot)!;
    const takes = e.placeholder ? otherTakes(e.shot) : s.takes;
    const usableTakes = takes.filter((t) => t.status === "ok" && t.has_video);
    const chosen = e.take != null ? takes.find((t) => t.take === e.take) : usableTakes[usableTakes.length - 1];
    const usable = !!chosen && chosen.status === "ok" && chosen.has_video;
    s.cut = {
      ...s.cut, take: e.take ?? chosen?.take ?? null, picked: e.take != null, pass: e.pass, placeholder: e.placeholder, usable,
      trim_in: e.trim_in, trim_out: e.trim_out, locked: e.locked, note: e.note, in_cut_file: e.in_cut_file,
      frames: usable ? chosen!.frames ?? null : null,
      order: i, script_index: sIdx.has(e.shot) ? sIdx.get(e.shot)! : null, out_of_order: ooo[i],
    };
    return s;
  });
}
