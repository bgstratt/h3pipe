// Pure helpers for the Script and Series config windows (Phase 9a): shot spans,
// check messages as editor diagnostics, and unified diffs for Promote.

import type { Text } from "@codemirror/state";
import type { SourceCheck, SourceFile, SourceMessage, SourceShotSpan } from "../types";

// ---------------------------------------------------------------------------
// shot spans
// ---------------------------------------------------------------------------

/**
 * The shots' line spans in a script, the way h3core/story.py records them: a
 * shot runs from its `## id` line to the last line that belongs to it (a field,
 * dialogue or action); blank and comment lines after that don't count. Used while
 * the buffer doesn't parse (the server's `shots` is empty then) and between checks.
 */
export function localShotSpans(text: string): SourceShotSpan[] {
  const out: SourceShotSpan[] = [];
  let cur: SourceShotSpan | null = null;
  const lines = text.split(/\r?\n/);
  for (let i = 0; i < lines.length; i++) {
    const n = i + 1;
    const line = lines[i].replace(/\s+$/, "");
    if (!line.trim() || line.trim().startsWith("//")) continue;
    if (line.startsWith("## ")) {
      if (cur) out.push(cur);
      const id = line.slice(3).trim().split(/\s+/)[0] ?? "";
      cur = id ? { id, line: n, end_line: n } : null;
      continue;
    }
    if (line.startsWith("# ") || line.startsWith("= ")) {
      if (cur) out.push(cur);
      cur = null;
      continue;
    }
    if (cur) cur.end_line = n;
  }
  if (cur) out.push(cur);
  return out;
}

/** The shot whose lines hold `line` (1-based), or null (between shots, a header). */
export function shotAtLine(spans: SourceShotSpan[], line: number): string | null {
  for (const s of spans) if (line >= s.line && line <= s.end_line) return s.id;
  return null;
}

/** The spans to trust for `text`: the check's when it was of this very text and
 * parsed (non-empty), else the local scan. */
export function spansFor(text: string, check: { text: string; result: SourceCheck } | null): SourceShotSpan[] {
  if (check && check.text === text && check.result.shots?.length) return check.result.shots;
  return localShotSpans(text);
}

// ---------------------------------------------------------------------------
// check messages
// ---------------------------------------------------------------------------

/**
 * Is a message about this window's file? The contract says each message has a
 * `file`; it may be the kind ("script" / "series"), the file's path or its name.
 * A message with no file belongs to the file that was checked.
 */
export function isOwnMessage(m: Pick<SourceMessage, "file">, file: SourceFile, path?: string | null): boolean {
  const f = (m.file ?? "").trim();
  if (!f || f === file) return true;
  if (f === "script" || f === "series") return false;
  const base = (p: string) => p.replace(/\\/g, "/").split("/").pop()!.toLowerCase();
  if (path && base(f) === base(path)) return true;
  // a name without a known path: the extension tells
  return file === "series" ? /\.json$/i.test(f) : /\.md$/i.test(f);
}

export type Severity = "error" | "warning";

export interface Diag {
  from: number;
  to: number;
  severity: Severity;
  message: string;
}

/** A check's messages for this file as editor diagnostics (offsets into `doc`).
 * A line past the end lands on the last line; a col underlines from there to the
 * end of the word, else the whole line. */
export function toDiagnostics(doc: Text, check: SourceCheck, file: SourceFile, path?: string | null): Diag[] {
  const out: Diag[] = [];
  const add = (m: SourceMessage, severity: Severity) => {
    // one with no line (a series config message naming no key) is listed instead
    if (!isOwnMessage(m, file, path) || m.line == null) return;
    const n = Math.min(Math.max(1, Math.floor(Number(m.line) || 1)), doc.lines);
    const line = doc.line(n);
    let from = line.from;
    let to = line.to;
    if (m.col != null && Number(m.col) >= 1) {
      from = Math.min(line.from + Math.floor(Number(m.col)) - 1, line.to);
      const rest = doc.sliceString(from, line.to);
      const w = /^\s*\S+/.exec(rest);
      to = w ? from + w[0].length : line.to;
      if (to === from && from > line.from) from -= 1; // at the end of a line: mark the last character
    }
    out.push({ from, to, severity, message: m.message });
  };
  for (const m of check.errors ?? []) add(m, "error");
  for (const m of check.warnings ?? []) add(m, "warning");
  return out;
}

/** The messages that aren't editor diagnostics, listed under the editor: the
 * other file's (`own: false`), and this file's with no line. */
export function listedMessages(check: SourceCheck | null, file: SourceFile, path?: string | null): (SourceMessage & { severity: Severity; own: boolean })[] {
  if (!check) return [];
  const pick = (ms: SourceMessage[] | undefined, severity: Severity) => (ms ?? [])
    .map((m) => ({ ...m, severity, own: isOwnMessage(m, file, path) }))
    .filter((m) => !m.own || m.line == null);
  return [...pick(check.errors, "error"), ...pick(check.warnings, "warning")];
}

/** "2 errors · 1 warning", or "" when clean. */
export function checkCounts(check: SourceCheck | null): string {
  if (!check) return "";
  const e = check.errors?.length ?? 0;
  const w = check.warnings?.length ?? 0;
  const parts: string[] = [];
  if (e) parts.push(`${e} error${e > 1 ? "s" : ""}`);
  if (w) parts.push(`${w} warning${w > 1 ? "s" : ""}`);
  return parts.join(" · ");
}

// ---------------------------------------------------------------------------
// unified diffs
// ---------------------------------------------------------------------------

export type DiffLineKind = "file" | "hunk" | "add" | "del" | "ctx" | "note";

export interface DiffLine {
  kind: DiffLineKind;
  text: string;
  /** line numbers in the old / new file (context, del / add) */
  old?: number;
  new?: number;
}

/** Split a unified diff into typed lines, numbering them from the hunk headers. */
export function parseUnifiedDiff(diff: string): DiffLine[] {
  const out: DiffLine[] = [];
  if (!diff) return out;
  let o = 0;
  let n = 0;
  // lines left in the current hunk (a removed "-- x" line isn't a file header)
  let oLeft = 0;
  let nLeft = 0;
  for (const raw of diff.replace(/\r\n/g, "\n").replace(/\n$/, "").split("\n")) {
    if (oLeft <= 0 && nLeft <= 0 && (raw.startsWith("--- ") || raw.startsWith("+++ "))) {
      out.push({ kind: "file", text: raw });
      continue;
    }
    const h = /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@/.exec(raw);
    if (h) {
      o = Number(h[1]);
      n = Number(h[3]);
      oLeft = h[2] != null ? Number(h[2]) : 1;
      nLeft = h[4] != null ? Number(h[4]) : 1;
      out.push({ kind: "hunk", text: raw });
      continue;
    }
    if (raw.startsWith("+")) {
      out.push({ kind: "add", text: raw.slice(1), new: n++ });
      nLeft--;
    } else if (raw.startsWith("-")) {
      out.push({ kind: "del", text: raw.slice(1), old: o++ });
      oLeft--;
    } else if (raw.startsWith(" ") || (raw === "" && (oLeft > 0 || nLeft > 0))) {
      out.push({ kind: "ctx", text: raw.slice(1), old: o++, new: n++ });
      oLeft--;
      nLeft--;
    } else out.push({ kind: "note", text: raw.replace(/^\\ ?/, "") });
  }
  return out;
}

/** +added / -removed line counts of a unified diff. */
export function diffStats(diff: string): { add: number; del: number } {
  let add = 0;
  let del = 0;
  for (const l of parseUnifiedDiff(diff)) {
    if (l.kind === "add") add++;
    else if (l.kind === "del") del++;
  }
  return { add, del };
}

// ---------------------------------------------------------------------------
// a line-level unified diff (the dev mock's promote plan; small files)
// ---------------------------------------------------------------------------

/** A unified diff of two texts, `context` lines around each change. */
export function unifiedDiff(a: string, b: string, nameA: string, nameB: string, context = 3): string {
  if (a === b) return "";
  const A = a.split("\n");
  const B = b.split("\n");
  // LCS table (the files are a few hundred lines)
  const m = A.length;
  const n = B.length;
  const L: Uint32Array[] = Array.from({ length: m + 1 }, () => new Uint32Array(n + 1));
  for (let i = m - 1; i >= 0; i--) for (let j = n - 1; j >= 0; j--) L[i][j] = A[i] === B[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
  type Op = { k: " " | "-" | "+"; s: string; i: number; j: number };
  const ops: Op[] = [];
  let i = 0;
  let j = 0;
  while (i < m || j < n) {
    if (i < m && j < n && A[i] === B[j]) ops.push({ k: " ", s: A[i], i: i++, j: j++ });
    // removals before additions, as diff -u writes them
    else if (i < m && (j >= n || L[i + 1][j] >= L[i][j + 1])) ops.push({ k: "-", s: A[i], i: i++, j });
    else ops.push({ k: "+", s: B[j], i, j: j++ });
  }
  const out = [`--- ${nameA}`, `+++ ${nameB}`];
  let x = 0;
  while (x < ops.length) {
    if (ops[x].k === " ") {
      x++;
      continue;
    }
    const start = Math.max(0, x - context);
    let end = x;
    // extend while changes are within 2*context of each other
    while (end < ops.length) {
      if (ops[end].k !== " ") {
        end++;
        continue;
      }
      let k = end;
      while (k < ops.length && ops[k].k === " ") k++;
      if (k < ops.length && k - end <= 2 * context) end = k;
      else break;
    }
    const stop = Math.min(ops.length, end + context);
    const hunk = ops.slice(start, stop);
    const oStart = hunk[0].i + 1;
    const nStart = hunk[0].j + 1;
    const oLen = hunk.filter((h) => h.k !== "+").length;
    const nLen = hunk.filter((h) => h.k !== "-").length;
    out.push(`@@ -${oStart},${oLen} +${nStart},${nLen} @@`);
    for (const h of hunk) out.push(h.k + h.s);
    x = stop;
  }
  return out.join("\n") + "\n";
}
