// Phase 9b in the mock: cut.json as h3takes keeps it (resolve_cut,
// cut_entry_to_json), the PUT /h3pipe/cut checks, and /cut/reset and /cut/copy.
// Pure; mockApi.ts holds the state.

import { GAIN_MAX, GAIN_MIN, audioOf, audioShot, audioWhy, normalizeAudio } from "../lib/audioSource";
import { applyOrder, outOfOrder } from "../lib/cutEdit";
import { trimWindow } from "../lib/playlist";
import type { CutAudioSource, CutEntry, CutWhat, Pass, ShotStatus } from "../types";

export const ENTRY_FIELDS = ["shot", "take", "pass", "trim_in", "trim_out", "locked", "note", "audio"];
/** Phase 9d: the keys a cut entry's `audio` may carry. */
export const AUDIO_FIELDS = ["source", "shot", "take", "pass", "path", "start", "offset", "gain"];

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
  /** Phase 9d: the clip's audio source (null: its own take's sound) */
  audio: CutAudioSource | null;
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
      audio: normalizeAudio(audioOf(raw), sid),
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
    out.splice(pos, 0, {
      shot: sid, pass, take: null, trim_in: 0, trim_out: 0, locked: false, note: "", in_cut_file: false, orphan: false,
      placeholder: false, audio: null,
    });
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
  if (e.audio) out.audio = e.audio;
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
/**
 * Phase 9d: the `audio` checks of PUT /h3pipe/cut — 400 for an unknown key, a
 * take that doesn't exist or has no sound, a path outside the episode or with
 * no audio stream, a negative start, or a gain outside 0–4. `soundOf` answers
 * the file a source would play (null: there is none).
 */
export function checkAudio(e: CutEntry, soundOf: (shot: string, a: CutAudioSource) => string | null): void {
  const raw = e.audio;
  if (raw == null) return;
  if (typeof raw !== "object" || Array.isArray(raw)) throw new CutError(`${e.shot}: audio must be an object or null`, 400);
  const a = raw as CutAudioSource & Record<string, unknown>;
  const bad = Object.keys(a).filter((k) => !AUDIO_FIELDS.includes(k));
  if (bad.length) throw new CutError(`${e.shot}: audio has unknown field ${bad.join(", ")}`, 400);
  if (a.source !== "take" && a.source !== "file" && a.source !== "none") {
    throw new CutError(`${e.shot}: audio.source must be take, file or none (got ${String(a.source)})`, 400);
  }
  if (a.source === "none") return;
  if (a.start != null && (typeof a.start !== "number" || !Number.isFinite(a.start) || a.start < 0)) {
    throw new CutError(`${e.shot}: audio.start must be 0 s or more (got ${String(a.start)})`, 400);
  }
  if (a.offset != null && (typeof a.offset !== "number" || !Number.isFinite(a.offset))) {
    throw new CutError(`${e.shot}: audio.offset must be a number of seconds (got ${String(a.offset)})`, 400);
  }
  if (a.gain != null && (typeof a.gain !== "number" || !Number.isFinite(a.gain) || a.gain < GAIN_MIN || a.gain > GAIN_MAX)) {
    throw new CutError(`${e.shot}: audio.gain must be between ${GAIN_MIN} and ${GAIN_MAX} (got ${String(a.gain)})`, 400);
  }
  if (a.source === "take") {
    if (a.take == null || !isInt(a.take)) throw new CutError(`${e.shot}: audio.take must be a take number`, 400);
    if (a.pass != null && a.pass !== "final" && a.pass !== "proxy") throw new CutError(`${e.shot}: audio has unknown pass ${String(a.pass)}`, 400);
  } else if (!a.path || typeof a.path !== "string") {
    throw new CutError(`${e.shot}: audio.path must be a file inside the episode`, 400);
  } else if (/^([a-zA-Z]:|[\\/])/.test(a.path) || a.path.split(/[\\/]/).includes("..")) {
    throw new CutError(`${e.shot}: ${a.path} is outside the episode`, 400);
  }
  if (!soundOf(e.shot, a)) {
    throw new CutError(
      a.source === "take"
        ? `${e.shot}: ${audioShot(a, e.shot)} t${String(a.take).padStart(2, "0")} doesn't exist or has no sound`
        : `${e.shot}: ${a.path} has no audio stream`,
      400,
    );
  }
}

export function checkEntries(
  entries: unknown, fps: number, framesOf: (e: CutEntry) => [number, number] | null,
  soundOf: (shot: string, a: CutAudioSource) => string | null = () => "ok",
): CutEntry[] {
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
    checkAudio(e, soundOf);
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

/** POST /h3pipe/cut/reset: script order (orphans after) and/or zero trims; picks, locks and notes
 * kept, and a locked entry keeps its trims (API.md "Phase 9b as built"). */
export function resetEntries(list: CutEntry[], pass: Pass, script: string[], what: CutWhat): CutEntry[] {
  let out = materialize(list, pass, script);
  if (what === "order" || what === "all") {
    const pos = new Map(script.map((s, i) => [s, i]));
    const inScript = out.filter((e) => pos.has(e.shot)).sort((a, b) => pos.get(a.shot)! - pos.get(b.shot)!);
    out = [...inScript, ...out.filter((e) => !pos.has(e.shot))];
  }
  if (what === "trims" || what === "all") out = out.map((e) => (e.locked ? e : dropTrims(e)));
  // Phase 9d: "audio" (and "all") puts every clip back to its own sound
  if (what === "audio" || what === "all") out = out.map((e) => (e.locked ? e : dropAudio(e)));
  return out;
}

function dropTrims(e: CutEntry): CutEntry {
  const out = { ...e };
  delete out.trim_in;
  delete out.trim_out;
  return out;
}

function dropAudio(e: CutEntry): CutEntry {
  const out = { ...e };
  delete out.audio;
  return out;
}

/** POST /h3pipe/cut/copy: the other pass's order and/or trims (converted by frame rate, cut down
 * to leave a frame of a take whose length `framesOf` knows, `trim_out` first); never picks, and
 * a locked entry keeps its own trims. */
export function copyEntries(
  from: CutEntry[], fromPass: Pass, fromFps: number, to: CutEntry[], toPass: Pass, toFps: number, script: string[], what: CutWhat,
  framesOf: (e: CutEntry) => [number, number] | null = () => null,
): CutEntry[] {
  const src = materialize(from, fromPass, script);
  let out = materialize(to, toPass, script);
  if (what === "order" || what === "all") {
    // a shot only the target has stays right after the entry it follows now
    out = applyOrder(out, src.map((e) => e.shot));
  }
  if (what === "all") {
    // Phase 9d: audio sources come with "all" only; a locked entry keeps its own
    const by = new Map(src.map((e) => [e.shot, e]));
    out = out.map((e) => (e.locked ? e : { ...dropAudio(e), ...(by.get(e.shot)?.audio ? { audio: by.get(e.shot)!.audio } : {}) }));
  }
  if (what === "trims" || what === "all") {
    const k = fromFps > 0 && toFps > 0 ? toFps / fromFps : 1;
    const by = new Map(src.map((e) => [e.shot, e]));
    out = out.map((e) => {
      if (e.locked) return e;
      const s = by.get(e.shot);
      let a = Math.round((s?.trim_in ?? 0) * k);
      let b = Math.round((s?.trim_out ?? 0) * k);
      const known = framesOf(e);
      if (known) {
        const total = known[1] > 0 && known[1] !== toFps ? Math.round((known[0] * toFps) / known[1]) : known[0];
        b = Math.max(0, Math.min(b, total - 1 - a));
        a = Math.max(0, Math.min(a, total - 1 - b));
      }
      return { ...dropTrims(e), ...(a ? { trim_in: a } : {}), ...(b ? { trim_out: b } : {}) };
    });
  }
  return out;
}

/** Orders the pass's statuses as the resolved cut and fills each `cut` (h3edit.episode_status).
 * `soundOf` (Phase 9d) answers what an audio source will actually play. */
export function applyCut(
  shots: ShotStatus[], list: CutEntry[], pass: Pass, script: string[], otherTakes: (shot: string) => ShotStatus["takes"],
  soundOf: (shot: string, a: CutAudioSource) => string | null = () => null,
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
      // Phase 9d: as stored, plus what will play and the badge's words
      audio: e.audio,
      audio_file: e.audio && e.audio.source !== "none" ? soundOf(e.shot, e.audio) : null,
      audio_why: e.audio ? audioWhy(e.audio, e.shot) : null,
    };
    return s;
  });
}
