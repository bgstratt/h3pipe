import type { ShotStatus, TakeSummary } from "../types";
import { missingOf, missingRefsTitle } from "./missingRefs";

export type BadgeKind =
  | "stale"
  | "override"
  | "override-stale"
  | "queued"
  | "rendering"
  | "placeholder"
  | "none"
  | "orphan"
  | "failed"
  | "unusable"
  | "missing-refs"
  | "target";

export interface Badge {
  kind: BadgeKind;
  label: string;
  title: string;
}

/** The take the cut uses for this shot (not for a placeholder: that's another pass). */
export function cutTake(s: ShotStatus): TakeSummary | undefined {
  if (s.cut.placeholder || s.cut.take == null) return undefined;
  return s.takes.find((t) => t.take === s.cut.take);
}

export function realStale(t: TakeSummary | { stale: string[] } | undefined): string[] {
  return (t?.stale ?? []).filter((r) => r !== "unknown");
}

const STALE_WHY: Record<string, string> = {
  script: "the script changed this shot since the take",
  ref: "a reference image changed since the take",
  preset: "model / LoRA / steps / size defaults changed since the take",
};

export function staleTitle(reasons: string[]): string {
  return reasons.map((r) => STALE_WHY[r] ?? r).join("; ");
}

/**
 * The bin/timeline badges for one shot. `rendering` holds take numbers of this
 * shot whose ComfyUI job is executing right now.
 */
export function shotBadges(s: ShotStatus, rendering: ReadonlySet<number> = new Set()): Badge[] {
  const out: Badge[] = [];
  if (s.orphan) out.push({ kind: "orphan", label: "orphan", title: "In cut.json but no longer in the script; assemble skips it" });
  const missing = missingOf(s);
  if (missing.length) {
    out.push({
      kind: "missing-refs",
      label: "missing refs",
      title: `A render needs ${missing.length} reference${missing.length > 1 ? "s" : ""} that aren't on disk; it's skipped unless you render anyway:
${missingRefsTitle(missing)}`,
    });
  }
  const queued = s.takes.filter((t) => t.status === "queued");
  const running = queued.filter((t) => rendering.has(t.take));
  if (running.length) {
    out.push({ kind: "rendering", label: "rendering", title: `Rendering ${running.map((t) => tn(t.take)).join(", ")}` });
  }
  if (queued.length > running.length) {
    const q = queued.filter((t) => !rendering.has(t.take));
    out.push({ kind: "queued", label: q.length > 1 ? `queued ×${q.length}` : "queued", title: `Queued: ${q.map((t) => tn(t.take)).join(", ")}` });
  }
  if (s.cut.placeholder) {
    out.push({
      kind: "placeholder",
      label: "placeholder",
      title: s.cut.take != null
        ? `The cut uses ${s.cut.pass} ${tn(s.cut.take)} as a stand-in`
        : `The cut wants a ${s.cut.pass} placeholder, and none is usable`,
    });
  } else if (!s.takes.length && !s.orphan) {
    out.push({ kind: "none", label: "no takes", title: "Nothing rendered yet" });
  }
  if (s.cut.picked && !s.cut.usable) {
    out.push({ kind: "unusable", label: "pick unusable", title: `cut.json picks ${s.cut.take != null ? tn(s.cut.take) : "a take"}, which isn't usable` });
  }
  const last = s.takes[s.takes.length - 1];
  if (last && last.status === "failed" && !queued.length) {
    out.push({ kind: "failed", label: "failed", title: `${tn(last.take)} failed${last.save_notes ? `: ${last.save_notes}` : ""}` });
  }
  const stale = realStale(cutTake(s));
  if (stale.length) out.push({ kind: "stale", label: `stale: ${stale.join(",")}`, title: staleTitle(stale) });
  if (s.override.fields.length) {
    out.push({ kind: "override", label: "override", title: `Override: ${s.override.fields.join(", ")}` });
    if (s.override.stale) {
      out.push({ kind: "override-stale", label: "override stale", title: "The override was written against an older build of this shot" });
    }
  }
  return out;
}

export function tn(take: number | null | undefined): string {
  return take == null ? "t--" : `t${String(take).padStart(2, "0")}`;
}

export interface SequenceGroup {
  sequence: string;
  shots: ShotStatus[];
  seconds: number;
  /** position of the first shot in the cut, a stable key when a sequence recurs */
  start: number;
}

/**
 * How long a shot lasts in the cut: its cut take's real frame count at that
 * take's fps (`cut.fps`: a Wan take is 16 fps), else the episode's, when the
 * server sends one (a `dur: model` take's length is the model's), else the
 * build's `seconds`. Null when neither is known.
 */
export function shotSeconds(s: ShotStatus, fps: number | undefined): number | null {
  const f = s.cut?.frames;
  const rate = s.cut?.fps || fps;
  if (f != null && f > 0) return f / (rate && rate > 0 ? rate : 24);
  return s.seconds;
}

/** Consecutive runs of one sequence, in cut order (the cut can reorder shots).
 * `fps` (the episode's) lets a shot count at its cut take's real length. */
export function groupBySequence(shots: ShotStatus[], fps?: number): SequenceGroup[] {
  const out: SequenceGroup[] = [];
  shots.forEach((s, i) => {
    const seq = s.sequence ?? (s.orphan ? "orphans" : "—");
    const secs = (fps ? shotSeconds(s, fps) : s.seconds) ?? 0;
    const last = out[out.length - 1];
    if (last && last.sequence === seq) {
      last.shots.push(s);
      last.seconds += secs;
    } else {
      out.push({ sequence: seq, shots: [s], seconds: secs, start: i });
    }
  });
  return out;
}

/** An episode-relative path (forward slashes) made absolute, in the episode's style. */
export function absPath(ep: string, rel: string): string {
  const win = ep.includes("\\");
  const sep = win ? "\\" : "/";
  const base = ep.replace(/[\\/]+$/, "");
  return base + sep + (win ? rel.replace(/\//g, "\\") : rel);
}

/** Two episode paths name the same folder (case and slashes don't matter on Windows). */
export function sameEp(a: string | null | undefined, b: string | null | undefined): boolean {
  if (!a || !b) return false;
  const n = (s: string) => s.replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();
  return n(a) === n(b);
}

export function fmtSeconds(s: number | null | undefined): string {
  if (s == null) return "";
  return `${s.toFixed(s < 10 ? 1 : 0)}s`;
}

export function fmtClock(t: number): string {
  if (!Number.isFinite(t) || t < 0) t = 0;
  const m = Math.floor(t / 60);
  const s = t - m * 60;
  return `${m}:${s.toFixed(2).padStart(5, "0")}`;
}

export function fmtWhen(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

/** Shorten a model/LoRA file name for a narrow column. */
export function shortName(name: string | null | undefined, max = 36): string {
  if (!name) return "";
  const base = name.split(/[\\/]/).pop() ?? name;
  return base.length > max ? base.slice(0, max - 1) + "…" : base;
}

/** A prompt as the loader joins it (a list of sections, or one string). */
export function promptText(p: string | string[] | null | undefined): string {
  return Array.isArray(p) ? p.join("\n\n") : p ?? "";
}
