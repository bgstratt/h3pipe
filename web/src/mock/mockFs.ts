// A fake folder tree for GET /h3pipe/browse on the dev page. The mock's one
// episode lives at C:\Shows\DeanStories\ep05; D:\Stock holds files to import.

import type { BrowseDir, BrowseFile, BrowseResult } from "../types";

interface Node {
  episode?: boolean;
  series_config?: boolean;
  dirs?: Record<string, Node>;
  files?: Record<string, number>; // name -> size in bytes
}

const TREE: Record<string, Node> = {
  "C:": {
    dirs: {
      Users: { dirs: { bgstr: { dirs: {
        ComfyProjects: { dirs: { scratch: {} } },
        Pictures: { files: { "ada_sketch.png": 402_311, "van_photo.jpg": 1_204_990, "notes.txt": 812 } },
      } } } },
      Shows: {
        dirs: {
          DeanStories: {
            series_config: true,
            dirs: {
              ep05: { episode: true, series_config: false, dirs: { shotlist: {}, renders_proxy: {} } },
              refs: { dirs: { _bg: {}, _takes: {} } },
              // Phase 9c: dialogue recordings to attach (one with a space in its name)
              audio: { files: { "ep05_dialogue.wav": 4_402_100, "ep05 take 2.wav": 4_511_880, "ep05_notes.txt": 620 } },
            },
            files: { "series.json": 9_120 },
          },
          KitchenSink: { series_config: true, dirs: { drafts: {} }, files: { "series.json": 4_310 } },
        },
      },
      AI: { dirs: { ComfyUI: { dirs: { models: {}, custom_nodes: {} } } } },
    },
  },
  "D:": {
    dirs: {
      Stock: {
        dirs: {
          portraits: { files: { "ada_drawn.png": 811_002, "bo_drawn.png": 790_331, "street_night.jpg": 2_331_870, "readme.md": 204 } },
          voices: { files: { "ada_take2.wav": 1_802_220, "bo_read.wav": 2_100_440 } },
        },
      },
    },
  },
};

export const HOME = "C:\\Users\\bgstr";

function parts(path: string): string[] {
  return path.split(/[\\/]+/).filter(Boolean);
}

function find(path: string): Node | null {
  const p = parts(path);
  if (!p.length) return null;
  let n: Node | undefined = TREE[p[0].toUpperCase()];
  for (const seg of p.slice(1)) {
    const next: Node | undefined = n?.dirs && Object.entries(n.dirs).find(([k]) => k.toLowerCase() === seg.toLowerCase())?.[1];
    n = next;
    if (!n) return null;
  }
  return n ?? null;
}

function join(base: string, name: string): string {
  return base.endsWith("\\") ? base + name : `${base}\\${name}`;
}

export function fsExists(path: string): boolean {
  return !!find(path);
}

export function fsFileExists(path: string): boolean {
  const p = parts(path);
  const dir = find(p.slice(0, -1).join("\\"));
  const name = p[p.length - 1]?.toLowerCase();
  return !!dir?.files && Object.keys(dir.files).some((f) => f.toLowerCase() === name);
}

export class FsError extends Error {
  constructor(message: string, public status: number) {
    super(message);
  }
}

/** The /h3pipe/browse answer. `files` (not in the contract yet) lists files too. */
export function browse(path: string | null | undefined, files: boolean): BrowseResult {
  if (!path) {
    const dirs: BrowseDir[] = [
      { name: `Home (${HOME})`, path: HOME, episode: false, series_config: false },
      { name: "C:\\", path: "C:\\", episode: false, series_config: false },
      { name: "D:\\", path: "D:\\", episode: false, series_config: false },
    ];
    return { path: "", parent: null, episode: false, truncated: false, dirs, ...(files ? { files: [] } : {}) };
  }
  const n = find(path);
  if (!n) throw new FsError(`${path} doesn't exist.`, 404);
  const p = parts(path);
  const norm = p.length === 1 ? `${p[0].toUpperCase()}\\` : [p[0].toUpperCase(), ...p.slice(1)].join("\\");
  const parent = p.length === 1 ? "" : p.length === 2 ? `${p[0].toUpperCase()}\\` : [p[0].toUpperCase(), ...p.slice(1, -1)].join("\\");
  const dirs: BrowseDir[] = Object.entries(n.dirs ?? {})
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([name, d]) => ({ name, path: join(norm, name), episode: !!d.episode, series_config: !!d.series_config || !!d.episode }));
  const out: BrowseResult = { path: norm, parent, episode: !!n.episode, truncated: false, dirs };
  if (files) {
    out.files = Object.entries(n.files ?? {})
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([name, size]): BrowseFile => ({ name, path: join(norm, name), size }));
  }
  return out;
}
