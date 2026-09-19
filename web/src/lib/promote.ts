// Promote (Phase 9a): the plan the server proposes, what's ticked, and the
// confirm round trip with its 409 re-plan. No React, so it's testable alone.

import { ApiError, errText, type Api } from "../api";
import type { PromoteItem, PromotePlan, PromoteResult } from "../types";

/** Every item ticked, keeping earlier choices for items still in the plan. */
export function initialChecks(plan: PromotePlan, prev: Record<string, boolean> = {}): Record<string, boolean> {
  return Object.fromEntries(plan.items.map((i) => [i.id, prev[i.id] ?? true]));
}

/** The ticked items' ids, in plan order. */
export function tickedIds(plan: PromotePlan, checked: Record<string, boolean>): string[] {
  return plan.items.filter((i) => checked[i.id] !== false).map((i) => i.id);
}

/** Where an item or a leftover belongs: "sh040", "episode", "ref dean (front)". */
export function scopeLabel(x: Pick<PromoteItem, "scope" | "shot" | "ref" | "view"> & { pass?: string | null }): string {
  const pass = x.pass ? ` · ${x.pass}` : "";
  if (x.scope === "shot") return (x.shot ?? "shot") + pass;
  if (x.scope === "ref") return `${(x.ref ?? "ref").replace(/^(subject|location|voice):/, "")}${x.view ? ` (${x.view})` : ""}${pass}`;
  return "episode" + pass;
}

/** A summary's `code` spans (the server quotes lines and keys in backticks). */
export function codeSpans(s: string): { text: string; code: boolean }[] {
  return s.split("`").map((text, i) => ({ text, code: i % 2 === 1 })).filter((p) => p.text);
}

/** A diff's leading notice lines (`# series.json wasn't formatted …`), and the rest. */
export function splitDiffNotice(diff: string): { notice: string[]; body: string } {
  const lines = diff.split("\n");
  let i = 0;
  while (i < lines.length && lines[i].startsWith("# ")) i++;
  return { notice: lines.slice(0, i).map((l) => l.slice(2)), body: lines.slice(i).join("\n") };
}

/** Where an item goes, for its badge. */
export function destLabel(i: PromoteItem, names: { script: string; series: string }): string {
  const f = i.dest === "script" ? names.script : names.series;
  return i.line ? `${f}:${i.line}` : f;
}

/** A value for display: strings as they are, the rest as compact JSON. */
export function valueText(v: unknown): string {
  if (v == null) return "—";
  if (typeof v === "string") return v;
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}

export type PromoteOutcome =
  | { kind: "done"; result: PromoteResult }
  | { kind: "replanned"; plan: PromotePlan; why: string }
  | { kind: "error"; message: string };

/**
 * POST /h3pipe/promote with the plan's hashes. A 409 (a file changed since the
 * plan) fetches a fresh plan for the same scope instead: the caller shows it
 * and asks again.
 */
export async function confirmPromote(api: Api, ep: string, shot: string | null, plan: PromotePlan, ids: string[]): Promise<PromoteOutcome> {
  try {
    const result = await api.promote(ep, ids, plan.hashes, shot);
    return { kind: "done", result };
  } catch (e) {
    if (e instanceof ApiError && e.status === 409) {
      try {
        const fresh = await api.promotePlan(ep, shot);
        return { kind: "replanned", plan: fresh, why: e.message };
      } catch (e2) {
        return { kind: "error", message: errText(e2) };
      }
    }
    return { kind: "error", message: errText(e) };
  }
}
