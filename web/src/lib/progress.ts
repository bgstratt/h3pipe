// A pass you can watch (docs/polish_Plan.md P1): how far through an episode a
// render pass is, how fast it is going, and how much is left.
//
// Everything here comes from take sidecars the server already sends — each take
// carries `queued` and `finished` — so nothing new has to be written or kept in
// sync. Pure functions; the components format them.
//
// The rate is WALL-CLOCK THROUGHPUT, not the average take duration. With 240
// prompts sitting in ComfyUI's queue a take's own queued -> finished is mostly
// waiting for the ones before it, so averaging that reads far slower than the
// pass actually goes. What a person experiences is finished takes per minute of
// real time, which is what this measures.

import type { ShotStatus, TakeSummary } from "../types";
import { realStale } from "./format";

/** Takes more than this far apart (seconds) belong to different runs, so
 * yesterday's takes never count towards today's rate. */
export const RUN_GAP_S = 15 * 60;

export interface PassProgress {
  /** shots with a finished, usable take */
  done: number;
  /** shots the pass has to render (orphans excluded: they aren't in the script) */
  total: number;
  /** takes still queued on ComfyUI */
  queued: number;
  /** shots whose newest take failed */
  failed: number;
  /** shots with no take at all and nothing queued */
  todo: number;
  /** seconds from the first queue to the last finish of the current run
   * (null: fewer than two finished takes in it) */
  elapsed_s: number | null;
  /** seconds of wall clock per finished take in this run (null: same) */
  per_shot_s: number | null;
  /** per_shot_s x whatever is left (null when there is no rate, 0 when done) */
  remaining_s: number | null;
  /** takes counted in the run (finished within it) */
  run_takes: number;
  /** true while anything is queued: the ETA means something */
  running: boolean;
}

function ms(t: string | null | undefined): number | null {
  if (!t) return null;
  const n = Date.parse(t);
  return Number.isNaN(n) ? null : n;
}

/** The newest take of a shot, by take number (what the bin shows). */
function newest(takes: TakeSummary[]): TakeSummary | undefined {
  return takes.reduce<TakeSummary | undefined>(
    (best, t) => (best == null || t.take > best.take ? t : best), undefined);
}

interface Span {
  queued: number | null;
  finished: number;
}

/**
 * The takes of the current run: walk finished takes newest-first and stop at the
 * first gap longer than RUN_GAP_S, so a pass that started after lunch isn't
 * averaged with the one before it.
 */
export function runSpans(shots: ShotStatus[], gap_s = RUN_GAP_S): Span[] {
  const all: Span[] = [];
  for (const s of shots) {
    for (const t of s.takes) {
      const fin = ms(t.finished);
      if (t.status === "ok" && fin != null) all.push({ queued: ms(t.queued), finished: fin });
    }
  }
  all.sort((a, b) => b.finished - a.finished);
  const run: Span[] = [];
  for (const span of all) {
    const last = run[run.length - 1];
    if (last && last.finished - span.finished > gap_s * 1000) break;
    run.push(span);
  }
  return run;
}

/** Where a pass stands. `shots` is one pass's shots, as GET /h3pipe/episode sends them. */
export function passProgress(shots: ShotStatus[] | null | undefined,
                            gap_s = RUN_GAP_S): PassProgress {
  const mine = (shots ?? []).filter((s) => !s.orphan);
  let done = 0;
  let queued = 0;
  let failed = 0;
  let todo = 0;
  for (const s of mine) {
    const usable = s.takes.some((t) => t.status === "ok");
    const q = s.takes.filter((t) => t.status === "queued").length;
    queued += q;
    if (usable) {
      done += 1;
    } else if (q) {
      // rendering now: neither done nor waiting to be asked for
    } else if (newest(s.takes)?.status === "failed") {
      failed += 1;
    } else {
      todo += 1;
    }
  }
  const run = runSpans(mine, gap_s);
  const starts = run.map((r) => r.queued ?? r.finished);
  const first = starts.length ? Math.min(...starts) : null;
  const last = run.length ? Math.max(...run.map((r) => r.finished)) : null;
  // one sample is not a rate: two finished takes are the minimum
  const enough = run.length >= 2 && first != null && last != null && last > first;
  const elapsed_s = enough ? (last! - first!) / 1000 : null;
  const per_shot_s = elapsed_s != null ? elapsed_s / run.length : null;
  const left = mine.length - done;
  const remaining_s = per_shot_s == null ? null : Math.max(0, per_shot_s * left);
  return {
    done, total: mine.length, queued, failed, todo,
    elapsed_s, per_shot_s, remaining_s,
    run_takes: run.length, running: queued > 0,
  };
}

/** "24m" / "1h 8m" / "45s" — a duration a person reads at a glance. */
export function fmtDuration(s: number | null | undefined): string {
  if (s == null || !Number.isFinite(s)) return "";
  const t = Math.max(0, Math.round(s));
  if (t < 90) return `${t}s`;
  const m = Math.round(t / 60);
  if (m < 60) return `${m}m`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

/** The footer's line: "37/240 · 12q · 24m · ~20s/shot · ~68m left". */
export function progressLine(p: PassProgress): string {
  const bits = [`${p.done}/${p.total}`];
  if (p.queued) bits.push(`${p.queued}q`);
  if (p.failed) bits.push(`${p.failed} failed`);
  if (p.elapsed_s != null) bits.push(fmtDuration(p.elapsed_s));
  if (p.per_shot_s != null) bits.push(`~${fmtDuration(p.per_shot_s)}/shot`);
  if (p.running && p.remaining_s != null) bits.push(`~${fmtDuration(p.remaining_s)} left`);
  return bits.join(" · ");
}

/** The tooltip: the arithmetic, so the estimate can be argued with. */
export function progressTitle(p: PassProgress): string {
  const lines = [`${p.done} of ${p.total} shots have a finished take`];
  if (p.queued) lines.push(`${p.queued} take(s) queued on ComfyUI`);
  if (p.failed) lines.push(`${p.failed} shot(s) whose newest take failed`);
  if (p.todo) lines.push(`${p.todo} shot(s) not asked for yet`);
  if (p.per_shot_s != null) {
    lines.push(`${p.run_takes} take(s) in this run finished over ${fmtDuration(p.elapsed_s)} `
      + `of wall clock, so ~${fmtDuration(p.per_shot_s)} a shot (queue waiting included, `
      + `which is what a pass really costs)`);
  }
  if (p.running && p.remaining_s != null) {
    lines.push(`${p.total - p.done} left at that rate: ~${fmtDuration(p.remaining_s)}`);
  } else if (p.per_shot_s != null) {
    lines.push("nothing queued, so no estimate — this is the last run's rate");
  } else {
    lines.push("no rate yet: two finished takes in one run are needed");
  }
  return lines.join("\n");
}


// ---------------------------------------------------------------------------
// what the pass still needs (P2)
// ---------------------------------------------------------------------------

/** Shots with no finished take and nothing queued: what "Render missing" asks for. */
export function missingShots(shots: ShotStatus[] | null | undefined): string[] {
  return (shots ?? [])
    .filter((s) => !s.orphan
      && !s.takes.some((t) => t.status === "ok" || t.status === "queued"))
    .map((s) => s.shot);
}

/**
 * Shots whose newest finished take is stale for a reason a re-render settles:
 * the script, a reference, the preset or the target changed since it rendered.
 *
 * `unknown` (a take from before sidecars carried provenance) is left out — it
 * isn't evidence of anything, so re-rendering it proves nothing. A shot with a
 * queued take is left out too: it is already being dealt with.
 */
export function staleShots(shots: ShotStatus[] | null | undefined): string[] {
  const out: string[] = [];
  for (const s of shots ?? []) {
    if (s.orphan || s.takes.some((t) => t.status === "queued")) continue;
    const newest = s.takes.filter((t) => t.status === "ok")
      .reduce<TakeSummary | undefined>((best, t) => (!best || t.take > best.take ? t : best),
                                       undefined);
    if (newest && realStale(newest).length) out.push(s.shot);
  }
  return out;
}

/** The reasons behind a stale list, for the button's tooltip: {script: 12, ref: 3}. */
export function staleReasons(shots: ShotStatus[] | null | undefined): Record<string, number> {
  const out: Record<string, number> = {};
  const wanted = new Set(staleShots(shots));
  for (const s of shots ?? []) {
    if (!wanted.has(s.shot)) continue;
    const newest = s.takes.filter((t) => t.status === "ok")
      .reduce<TakeSummary | undefined>((best, t) => (!best || t.take > best.take ? t : best),
                                       undefined);
    for (const r of realStale(newest)) out[r] = (out[r] ?? 0) + 1;
  }
  return out;
}
