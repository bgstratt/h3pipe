// P5 (docs/polish_Plan.md): naming a new episode. The same rules the server
// applies (h3source.check_name), so the dialog can say why before asking, plus
// whether the folder it would go in is somewhere the editor can find it again.

import { depthBelow, reachable } from "./browse";

const NAME_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
// names find_episodes walks past, and Windows' device names: an episode called
// one of these could never be opened again
const RESERVED = new Set([
  "refs", "renders", "renders_proxy", "shotlist", "views", "audio", "targets",
  "con", "prn", "aux", "nul",
  ...Array.from({ length: 9 }, (_, i) => `com${i + 1}`),
  ...Array.from({ length: 9 }, (_, i) => `lpt${i + 1}`),
]);

/** Why this name can't be an episode folder, or null when it can. */
export function nameError(name: string): string | null {
  const n = name.trim();
  if (!n) return "Give the episode a name — it becomes the folder and the script (ep02 → ep02.md).";
  if (!NAME_RE.test(n)) {
    return "Letters, digits, dot, dash or underscore only, starting with a letter or digit.";
  }
  if (n.endsWith(".")) return "A folder name can't end with a dot.";
  if (RESERVED.has(n.toLowerCase())) return `${n} is a reserved folder name.`;
  return null;
}

/** The next name to offer, from the folders already there: ep07 after ep06,
 *  keeping the digits' width. "ep01" when none of them is numbered. */
export function suggestName(dirs: { name: string }[]): string {
  let best: { n: number; width: number } | null = null;
  for (const d of dirs) {
    const m = /^ep(\d+)$/i.exec(d.name);
    if (!m) continue;
    const n = parseInt(m[1], 10);
    if (!best || n >= best.n) best = { n, width: m[1].length };
  }
  if (!best) return "ep01";
  return `ep${String(best.n + 1).padStart(best.width, "0")}`;
}

/** Whether a folder is inside a project root at all (the server's 403). */
export function insideRoot(folder: string, roots: string[]): boolean {
  return roots.some((r) => depthBelow(folder, r) >= 0);
}

/** Whether an episode made in `folder` would be listed (episodes are found up
 *  to two levels below a root, so the folder itself may be at most one). */
export function willBeListed(folder: string, roots: string[]): boolean {
  return reachable(`${folder.replace(/[\\/]+$/, "")}/x`, roots);
}
