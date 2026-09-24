// P8: the files in a drop, including the ones inside a dropped folder.
//
// A browser hands a dropped folder over as a DataTransferItem entry, not a File,
// so `dataTransfer.files` alone silently ignores it. The entries have to be
// taken synchronously, before the drop event is over, which is why
// `filesFromDrag` collects them first and only then reads them.

/** The slice of the webkitGetAsEntry API this needs (so tests can fake it). */
export interface DropEntry {
  isFile?: boolean;
  isDirectory?: boolean;
  name?: string;
  file?: (ok: (f: File) => void, err?: (e: unknown) => void) => void;
  createReader?: () => { readEntries: (ok: (e: DropEntry[]) => void, err?: (e: unknown) => void) => void };
}

export interface DragLike {
  items?: ArrayLike<{ webkitGetAsEntry?: () => DropEntry | null }>;
  files?: ArrayLike<File>;
}

/** How deep into dropped folders to look, and how many files to take at once. */
export const DROP_DEPTH = 2;
export const DROP_LIMIT = 500;

function readFile(entry: DropEntry): Promise<File | null> {
  return new Promise((resolve) => {
    if (!entry.file) return resolve(null);
    try {
      entry.file((f) => resolve(f), () => resolve(null));
    } catch {
      resolve(null);
    }
  });
}

function readDir(entry: DropEntry): Promise<DropEntry[]> {
  const reader = entry.createReader?.();
  if (!reader) return Promise.resolve([]);
  // readEntries hands over a page at a time and an empty page means the end
  return new Promise((resolve) => {
    const all: DropEntry[] = [];
    const next = () => {
      try {
        reader.readEntries((batch) => {
          if (!batch.length) return resolve(all);
          all.push(...batch);
          if (all.length >= DROP_LIMIT) return resolve(all);
          next();
        }, () => resolve(all));
      } catch {
        resolve(all);
      }
    };
    next();
  });
}

async function walk(entries: DropEntry[], depth: number, out: File[]): Promise<void> {
  for (const e of entries) {
    if (out.length >= DROP_LIMIT) return;
    if (e.isFile) {
      const f = await readFile(e);
      if (f) out.push(f);
    } else if (e.isDirectory && depth > 0) {
      await walk(await readDir(e), depth - 1, out);
    }
  }
}

/**
 * Every file in a drop: the plain ones, plus what is inside any dropped folder
 * (to DROP_DEPTH levels, DROP_LIMIT files). Falls back to `dataTransfer.files`
 * where the entry API isn't there, so a browser without it still works for
 * files.
 */
export async function filesFromDrag(dt: DragLike | null | undefined): Promise<File[]> {
  if (!dt) return [];
  // synchronous, while the drop event is still alive
  const items = Array.from(dt.items ?? []);
  const entries = items.map((i) => {
    try {
      return i.webkitGetAsEntry?.() ?? null;
    } catch {
      return null;
    }
  }).filter((e): e is DropEntry => !!e);
  const plain = Array.from(dt.files ?? []);
  if (!entries.length) return plain.slice(0, DROP_LIMIT);
  const out: File[] = [];
  await walk(entries, DROP_DEPTH, out);
  // a browser that gave entries but no readable files (older WebKit): keep what
  // `files` had rather than dropping the whole thing on the floor
  return (out.length ? out : plain).slice(0, DROP_LIMIT);
}
