// Missing references per shot (API.md "Missing references"): badges, the
// episode summary, and which shots a render request will skip.

import type { MissingRef, ShotStatus } from "../types";

export function missingOf(s: Pick<ShotStatus, "missing_refs">): MissingRef[] {
  return s.missing_refs ?? [];
}

/** Refs the shot can't be rendered without, even with render anyway (`anyway: false`). */
export function noAnyway(refs: MissingRef[]): MissingRef[] {
  return refs.filter((r) => r.anyway === false);
}

/** "Picture 4: refs/_bg/x.png (dean)" per line, for tooltips. */
export function missingRefsTitle(refs: MissingRef[]): string {
  return refs.map((r) => `${r.slot}: ${r.path}${r.subject ? ` (${r.subject})` : ""}`).join("\n");
}

export interface MissingFile {
  path: string;
  kind: MissingRef["kind"];
  subject?: string;
  slots: string[];
  shots: string[];
}

export interface MissingSummary {
  /** shots missing at least one ref, in cut order */
  shots: string[];
  /** distinct missing files, the one blocking most shots first */
  files: MissingFile[];
  text: string;
}

/** Normalised path, for matching a ref's path to a shot's missing_refs. */
export function normPath(p: string | null | undefined): string {
  if (!p) return "";
  return p.replace(/\\/g, "/").replace(/^\.\//, "").toLowerCase();
}

/** The episode-level warning: which shots are blocked and by which files. */
export function missingRefsSummary(shots: ShotStatus[]): MissingSummary {
  const blocked: string[] = [];
  const byPath = new Map<string, MissingFile>();
  for (const s of shots) {
    if (s.orphan) continue;
    const m = missingOf(s);
    if (!m.length) continue;
    blocked.push(s.shot);
    for (const r of m) {
      const key = normPath(r.path);
      let f = byPath.get(key);
      if (!f) {
        f = { path: r.path, kind: r.kind, subject: r.subject, slots: [], shots: [] };
        byPath.set(key, f);
      }
      if (!f.slots.includes(r.slot)) f.slots.push(r.slot);
      if (!f.shots.includes(s.shot)) f.shots.push(s.shot);
    }
  }
  const files = [...byPath.values()].sort((a, b) => b.shots.length - a.shots.length || a.path.localeCompare(b.path));
  const n = blocked.length;
  const text = n === 0
    ? ""
    : `${n} shot${n === 1 ? " is" : "s are"} missing refs (${files.length} file${files.length === 1 ? "" : "s"})`;
  return { shots: blocked, files, text };
}

/** Split the shots of a render request into ready and blocked (missing refs). */
export function splitByMissingRefs(all: ShotStatus[], shots: string[]): { ready: string[]; blocked: { shot: string; refs: MissingRef[] }[] } {
  const ready: string[] = [];
  const blocked: { shot: string; refs: MissingRef[] }[] = [];
  for (const id of shots) {
    const s = all.find((x) => x.shot === id);
    const m = s ? missingOf(s) : [];
    if (m.length) blocked.push({ shot: id, refs: m });
    else ready.push(id);
  }
  return { ready, blocked };
}
