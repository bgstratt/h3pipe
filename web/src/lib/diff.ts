// A small word-level diff (LCS) for "override prompt vs built prompt". Prompts are a
// few thousand characters, so O(n*m) on words is fine; past a size limit it falls
// back to lines so the UI never stalls.

export type DiffOp = "eq" | "add" | "del";
export interface DiffPart {
  op: DiffOp;
  text: string;
}

const MAX_CELLS = 4_000_000;

/** Words and the whitespace between them, as separate tokens. */
export function tokenize(s: string): string[] {
  return s.match(/\s+|[^\s]+/g) ?? [];
}

/** Diff `a` (old: the built prompt) against `b` (new: the override). */
export function diffWords(a: string, b: string): DiffPart[] {
  let ta = tokenize(a);
  let tb = tokenize(b);
  if (ta.length * tb.length > MAX_CELLS) {
    ta = a.split(/(?<=\n)/);
    tb = b.split(/(?<=\n)/);
  }
  return merge(lcsDiff(ta, tb));
}

function lcsDiff(a: string[], b: string[]): DiffPart[] {
  // trim the common prefix and suffix first: most edits are local
  let pre = 0;
  while (pre < a.length && pre < b.length && a[pre] === b[pre]) pre++;
  let suf = 0;
  while (suf < a.length - pre && suf < b.length - pre && a[a.length - 1 - suf] === b[b.length - 1 - suf]) suf++;
  const A = a.slice(pre, a.length - suf);
  const B = b.slice(pre, b.length - suf);
  const n = A.length;
  const m = B.length;
  const out: DiffPart[] = [];
  for (let i = 0; i < pre; i++) out.push({ op: "eq", text: a[i] });
  if (n * m > MAX_CELLS) {
    // still too big: one delete and one add
    if (n) out.push({ op: "del", text: A.join("") });
    if (m) out.push({ op: "add", text: B.join("") });
  } else {
    // L[i][j] = LCS length of A[i..] and B[j..]
    const w = m + 1;
    const L = new Uint32Array((n + 1) * w);
    for (let i = n - 1; i >= 0; i--) {
      for (let j = m - 1; j >= 0; j--) {
        L[i * w + j] = A[i] === B[j] ? L[(i + 1) * w + j + 1] + 1 : Math.max(L[(i + 1) * w + j], L[i * w + j + 1]);
      }
    }
    let i = 0;
    let j = 0;
    while (i < n && j < m) {
      if (A[i] === B[j]) {
        out.push({ op: "eq", text: A[i] });
        i++;
        j++;
      } else if (L[(i + 1) * w + j] >= L[i * w + j + 1]) {
        out.push({ op: "del", text: A[i++] });
      } else {
        out.push({ op: "add", text: B[j++] });
      }
    }
    while (i < n) out.push({ op: "del", text: A[i++] });
    while (j < m) out.push({ op: "add", text: B[j++] });
  }
  for (let i = a.length - suf; i < a.length; i++) out.push({ op: "eq", text: a[i] });
  return out;
}

/** Join runs of the same op. */
function merge(parts: DiffPart[]): DiffPart[] {
  const out: DiffPart[] = [];
  for (const p of parts) {
    const last = out[out.length - 1];
    if (last && last.op === p.op) last.text += p.text;
    else out.push({ ...p });
  }
  return out;
}

/** Undo a diff: the old text (eq + del) or the new text (eq + add). */
export function applySide(parts: DiffPart[], side: "old" | "new"): string {
  const skip: DiffOp = side === "old" ? "add" : "del";
  return parts.filter((p) => p.op !== skip).map((p) => p.text).join("");
}

export function diffStats(parts: DiffPart[]): { added: number; removed: number } {
  let added = 0;
  let removed = 0;
  for (const p of parts) {
    const words = (p.text.match(/[^\s]+/g) ?? []).length;
    if (p.op === "add") added += words;
    else if (p.op === "del") removed += words;
  }
  return { added, removed };
}
