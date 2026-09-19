// Phase 9b: editing the cut (order, trims, locks) as whole-list PUT /h3pipe/cut
// bodies. Pure: the actions apply these to the store optimistically, then PUT.
//
// A pass's cut in `GET /h3pipe/episode` is already reconciled (h3takes.resolve_cut):
// every shot in cut order, with the entry's take / pass / trims / lock / note. An
// edit writes that whole list back with one thing changed, exactly as
// h3takes.cut_entry_to_json would (a take only when picked, a pass only for a
// placeholder, zero trims and empty notes left out), so nothing else moves.

import type { CutEntry, EpisodeStatus, Pass, ShotStatus } from "../types";

/** The cut.json entry a shot's status stands for, in `listPass`'s list. */
export function entryOf(s: ShotStatus, listPass: Pass): CutEntry {
  const c = s.cut;
  const e: CutEntry = { shot: s.shot };
  if (c?.pass && c.pass !== listPass) e.pass = c.pass;
  if (c?.picked && c.take != null) e.take = c.take;
  const a = frameInt(c?.trim_in);
  const b = frameInt(c?.trim_out);
  if (a) e.trim_in = a;
  if (b) e.trim_out = b;
  if (c?.locked) e.locked = true;
  if (c?.note) e.note = c.note;
  return e;
}

/** The whole pass's cut as PUT /h3pipe/cut entries, in cut order (orphans kept). */
export function entriesOf(st: EpisodeStatus): CutEntry[] {
  return st.shots.map((s) => entryOf(s, st.pass));
}

function frameInt(n: unknown): number {
  return typeof n === "number" && Number.isFinite(n) && n > 0 ? Math.floor(n) : 0;
}

// ---------------------------------------------------------------------------
// order
// ---------------------------------------------------------------------------

/**
 * Move `shot` to insertion point `to` (0..n, a gap in the list as it is now:
 * 0 = before the first clip, n = after the last). Returns null when that
 * leaves the order as it is (or the shot isn't there).
 */
export function moveTo<T extends { shot: string }>(list: T[], shot: string, to: number): T[] | null {
  const from = list.findIndex((e) => e.shot === shot);
  if (from < 0) return null;
  const gap = Math.min(list.length, Math.max(0, Math.round(to)));
  // the gaps either side of the clip itself leave it where it is
  if (gap === from || gap === from + 1) return null;
  const out = list.slice();
  const [e] = out.splice(from, 1);
  out.splice(gap > from ? gap - 1 : gap, 0, e);
  return out;
}

/** Move `shot` one place left (-1) or right (+1); null at either end. */
export function nudge<T extends { shot: string }>(list: T[], shot: string, delta: -1 | 1): T[] | null {
  const from = list.findIndex((e) => e.shot === shot);
  if (from < 0) return null;
  const to = delta < 0 ? from - 1 : from + 2;
  if (to < 0 || to > list.length) return null;
  return moveTo(list, shot, to);
}

/**
 * Put `list` in `order`. Shots `order` doesn't name keep their place after the
 * shot they follow now (so an undo written before a new shot appeared still
 * applies); shots `order` names that aren't in `list` are ignored.
 */
export function applyOrder<T extends { shot: string }>(list: T[], order: string[]): T[] {
  const by = new Map(list.map((e) => [e.shot, e]));
  const named = new Set(order.filter((s) => by.has(s)));
  const out: T[] = order.filter((s) => by.has(s)).map((s) => by.get(s)!);
  // unnamed shots: after the nearest earlier shot of the old list that is placed
  list.forEach((e, i) => {
    if (named.has(e.shot)) return;
    let at = 0;
    for (let j = i - 1; j >= 0; j--) {
      const k = out.indexOf(list[j]);
      if (k >= 0) {
        at = k + 1;
        break;
      }
    }
    out.splice(at, 0, e);
  });
  return out;
}

/**
 * The insertion point nearest `x` given the clips' [left, right] edges in
 * order (any one coordinate system): 0 before the first, n after the last.
 */
export function dropIndex(edges: { left: number; right: number }[], x: number): number {
  if (!edges.length) return 0;
  let best = 0;
  let dist = Infinity;
  for (let i = 0; i <= edges.length; i++) {
    const at = i === 0 ? edges[0].left : i === edges.length ? edges[i - 1].right : (edges[i - 1].right + edges[i].left) / 2;
    const d = Math.abs(x - at);
    if (d < dist) {
      dist = d;
      best = i;
    }
  }
  return best;
}

/**
 * Which shots are out of script order: the fewest shots whose moving explains
 * the order (everything off the longest run that is in script order). Orphans
 * (null index) are never flagged. The server's `out_of_order` wins when it
 * sends one; this is for the optimistic update and older servers.
 */
export function outOfOrder(scriptIndex: (number | null | undefined)[]): boolean[] {
  const idx: number[] = [];
  const pos: number[] = [];
  scriptIndex.forEach((v, i) => {
    if (typeof v === "number" && Number.isFinite(v)) {
      idx.push(v);
      pos.push(i);
    }
  });
  // longest strictly increasing subsequence (patience sorting, with parents)
  const tails: number[] = [];
  const parent: number[] = new Array(idx.length).fill(-1);
  for (let i = 0; i < idx.length; i++) {
    let lo = 0;
    let hi = tails.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (idx[tails[mid]] < idx[i]) lo = mid + 1;
      else hi = mid;
    }
    parent[i] = lo > 0 ? tails[lo - 1] : -1;
    tails[lo] = i;
  }
  const keep = new Set<number>();
  for (let k = tails.length ? tails[tails.length - 1] : -1; k >= 0; k = parent[k]) keep.add(k);
  const out = scriptIndex.map(() => false);
  for (let i = 0; i < idx.length; i++) if (!keep.has(i)) out[pos[i]] = true;
  return out;
}

/** Some shot is out of script order (the server's flags, else from `script_index`). */
export function anyOutOfOrder(st: EpisodeStatus | undefined): boolean {
  if (!st) return false;
  const shots = st.shots.filter((s) => !s.orphan);
  if (shots.some((s) => typeof s.cut?.out_of_order === "boolean")) return shots.some((s) => s.cut.out_of_order === true);
  if (!shots.some((s) => typeof s.cut?.script_index === "number")) return false;
  return outOfOrder(shots.map((s) => s.cut?.script_index)).some(Boolean);
}

/** The shots in script order, when the server says it (`cut.script_index`); null otherwise. */
export function scriptOrder(st: EpisodeStatus): string[] | null {
  const withIdx = st.shots.filter((s) => typeof s.cut?.script_index === "number");
  if (!withIdx.length) return null;
  return withIdx.slice().sort((a, b) => (a.cut.script_index as number) - (b.cut.script_index as number)).map((s) => s.shot);
}

/** The shot is out of order: the server's flag, else worked out from `script_index`. */
export function isOutOfOrder(st: EpisodeStatus, shot: string): boolean {
  const s = st.shots.find((x) => x.shot === shot);
  if (!s) return false;
  if (typeof s.cut?.out_of_order === "boolean") return s.cut.out_of_order;
  if (typeof s.cut?.script_index !== "number") return false;
  const flags = outOfOrder(st.shots.map((x) => x.cut?.script_index));
  return flags[st.shots.indexOf(s)];
}

// ---------------------------------------------------------------------------
// trims
// ---------------------------------------------------------------------------

/** Pointer pixels to whole frames at `zoom` px per second. */
export function pxToFrames(dx: number, zoom: number, fps: number): number {
  if (!(zoom > 0) || !Number.isFinite(dx)) return 0;
  const n = Math.round((dx / zoom) * (fps > 0 ? fps : 24));
  return n === 0 ? 0 : n; // no -0
}

/**
 * The most each side can be trimmed with the other side as it is, leaving at
 * least one frame. `total` is the clip's length in the cut's frames (it can be
 * fractional for a clip at another frame rate); null when unknown.
 */
export function trimLimits(total: number | null, trimIn: number, trimOut: number): { maxIn: number; maxOut: number } {
  if (total == null || !(total > 0)) return { maxIn: Infinity, maxOut: Infinity };
  const eps = 1e-6;
  return {
    maxIn: Math.max(0, Math.floor(total - Math.max(0, trimOut) - 1 + eps)),
    maxOut: Math.max(0, Math.floor(total - Math.max(0, trimIn) - 1 + eps)),
  };
}

/** One side's trim, clamped to 0..its limit (whole frames). */
export function clampTrim(side: "in" | "out", want: number, total: number | null, trimIn: number, trimOut: number): number {
  const { maxIn, maxOut } = trimLimits(total, trimIn, trimOut);
  const v = Number.isFinite(want) ? Math.round(want) : 0;
  return Math.min(side === "in" ? maxIn : maxOut, Math.max(0, v));
}

/** Frames and seconds, for the trim tooltip and the inspector. */
export function framesLabel(frames: number, fps: number): string {
  const f = fps > 0 ? fps : 24;
  return `${frames} f · ${(frames / f).toFixed(2)} s`;
}

// ---------------------------------------------------------------------------
// entries
// ---------------------------------------------------------------------------

export interface CutFields {
  trim_in: number;
  trim_out: number;
  locked: boolean;
}

export function fieldsOf(e: CutEntry | undefined): CutFields {
  return { trim_in: frameInt(e?.trim_in), trim_out: frameInt(e?.trim_out), locked: !!e?.locked };
}

/** `entries` with one shot's trims / lock changed (zero trims and false left out). */
export function withFields(entries: CutEntry[], shot: string, patch: Partial<CutFields>): CutEntry[] {
  return entries.map((e) => {
    if (e.shot !== shot) return e;
    const f = { ...fieldsOf(e), ...patch };
    const out: CutEntry = { ...e };
    delete out.trim_in;
    delete out.trim_out;
    delete out.locked;
    delete out.note;
    if (f.trim_in > 0) out.trim_in = Math.floor(f.trim_in);
    if (f.trim_out > 0) out.trim_out = Math.floor(f.trim_out);
    if (f.locked) out.locked = true;
    if (e.note) out.note = e.note;
    return out;
  });
}

/** Every trim set to zero. */
export function clearTrims(entries: CutEntry[]): CutEntry[] {
  return entries.reduce((list, e) => (e.trim_in || e.trim_out ? withFields(list, e.shot, { trim_in: 0, trim_out: 0 }) : list), entries);
}

// ---------------------------------------------------------------------------
// the status, updated in place of the server (optimistic)
// ---------------------------------------------------------------------------

/**
 * The episode status as it will read once `entries` are saved: shots in the
 * new order with their new trims and locks. Takes and picks are the status's
 * own (an edit never changes them). `order` and, when the server sends
 * `script_index`, `out_of_order` are worked out again.
 */
export function applyEntries(st: EpisodeStatus, entries: CutEntry[]): EpisodeStatus {
  const by = new Map(entries.map((e) => [e.shot, e]));
  const shots = applyOrder(st.shots, entries.map((e) => e.shot));
  const hasIdx = shots.some((s) => typeof s.cut?.script_index === "number");
  const ooo = hasIdx ? outOfOrder(shots.map((s) => s.cut?.script_index)) : null;
  return {
    ...st,
    shots: shots.map((s, i) => {
      const e = by.get(s.shot);
      const f = e ? fieldsOf(e) : { trim_in: s.cut.trim_in, trim_out: s.cut.trim_out, locked: s.cut.locked };
      const cut = { ...s.cut, trim_in: f.trim_in, trim_out: f.trim_out, locked: f.locked, in_cut_file: e ? true : s.cut.in_cut_file };
      if (s.cut.order != null || hasIdx) cut.order = i;
      if (ooo) cut.out_of_order = ooo[i];
      return { ...s, cut };
    }),
  };
}

/** Two entry lists write the same file. */
export function sameEntries(a: CutEntry[], b: CutEntry[]): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    const x = a[i];
    const y = b[i];
    if (x.shot !== y.shot || (x.take ?? null) !== (y.take ?? null) || (x.pass ?? null) !== (y.pass ?? null)) return false;
    const fx = fieldsOf(x);
    const fy = fieldsOf(y);
    if (fx.trim_in !== fy.trim_in || fx.trim_out !== fy.trim_out || fx.locked !== fy.locked || (x.note ?? "") !== (y.note ?? "")) return false;
  }
  return true;
}
