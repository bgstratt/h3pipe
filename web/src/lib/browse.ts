// Folder browser helpers: breadcrumbs for Windows and POSIX paths, and root checks.

export interface Crumb {
  label: string;
  path: string;
}

/** "C:\Users\me" -> C:\ · Users · me, each with the path to browse to. */
export function crumbs(path: string | null | undefined): Crumb[] {
  if (!path) return [];
  const unc = path.startsWith("\\\\");
  const win = unc || /^[A-Za-z]:/.test(path) || path.includes("\\");
  const parts = path.split(/[\\/]+/).filter(Boolean);
  const out: Crumb[] = [];
  if (!win) {
    out.push({ label: "/", path: "/" });
    let acc = "";
    for (const p of parts) {
      acc += "/" + p;
      out.push({ label: p, path: acc });
    }
    return out;
  }
  let acc: string;
  let rest: string[];
  if (unc) {
    acc = "\\\\" + parts.slice(0, 2).join("\\");
    out.push({ label: acc, path: acc + "\\" });
    rest = parts.slice(2);
  } else {
    acc = parts[0]; // "C:"
    out.push({ label: acc + "\\", path: acc + "\\" });
    rest = parts.slice(1);
  }
  for (const p of rest) {
    acc += "\\" + p;
    out.push({ label: p, path: acc });
  }
  return out;
}

function norm(p: string): string {
  return p.replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();
}

/** Levels `path` sits below `root` (0 = the root itself), or -1 if it isn't inside. */
export function depthBelow(path: string, root: string): number {
  const p = norm(path);
  const r = norm(root);
  if (p === r) return 0;
  if (!p.startsWith(r + "/")) return -1;
  return p.slice(r.length + 1).split("/").length;
}

/** Whether `find_episodes` sees an episode folder: up to two levels below a root. */
export function reachable(ep: string, roots: string[], maxDepth = 2): boolean {
  return roots.some((r) => {
    const d = depthBelow(ep, r);
    return d >= 0 && d <= maxDepth;
  });
}

export function sameDir(a: string, b: string): boolean {
  return norm(a) === norm(b);
}

/**
 * Phase 9d: an absolute path as the routes want it — relative to the episode,
 * forward slashes, or `../…` for a file beside a parent-folder series config.
 * Null when it is somewhere else entirely (/h3pipe/file can't serve it).
 */
export function epRelative(path: string, ep: string): string | null {
  const p = path.replace(/\\/g, "/").replace(/\/+$/, "");
  const root = ep.replace(/\\/g, "/").replace(/\/+$/, "");
  const inside = (base: string): string | null => {
    const b = base.toLowerCase();
    const l = p.toLowerCase();
    return l.startsWith(b + "/") ? p.slice(base.length + 1) : null;
  };
  const here = inside(root);
  if (here) return here;
  const parent = root.slice(0, root.lastIndexOf("/"));
  if (!parent || parent === root) return null;
  const up = inside(parent);
  return up ? `../${up}` : null;
}

export const IMAGE_EXT = /\.(png|jpe?g|webp|gif|bmp)$/i;
export const AUDIO_EXT = /\.(wav|mp3|flac|ogg|m4a)$/i;
/** Phase 9d: a clip's audio may also come from a rendered take (an mp4). */
export const CLIP_AUDIO_EXT = /\.(wav|mp3|flac|ogg|m4a|aac|opus|mp4|mov|mkv|webm)$/i;
