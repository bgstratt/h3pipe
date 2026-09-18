// Hover scrub through a take's strip image: STRIP_FRAMES cells of equal width,
// left to right, evenly spaced through the clip (docs/API.md).

export const STRIP_FRAMES = 8;

/** The cell under a pointer at `x` (px from the left) of a box `width` px wide. */
export function stripCell(x: number, width: number, cells = STRIP_FRAMES): number {
  if (!(width > 0) || cells < 1 || !Number.isFinite(x)) return 0;
  const i = Math.floor((x / width) * cells);
  return Math.min(cells - 1, Math.max(0, i));
}

/**
 * CSS for showing one cell of the strip filling its box: the image is scaled to
 * `cells` box widths, and background-position picks the cell. With a
 * percentage position p, the image's left edge sits at p * (box - image), so
 * cell i is at p = i / (cells - 1).
 */
export function stripStyle(url: string, cell: number, cells = STRIP_FRAMES): {
  backgroundImage: string;
  backgroundSize: string;
  backgroundPosition: string;
} {
  const c = Math.min(cells - 1, Math.max(0, Math.floor(cell)));
  const pos = cells > 1 ? (c / (cells - 1)) * 100 : 0;
  return {
    backgroundImage: `url("${url}")`,
    backgroundSize: `${cells * 100}% 100%`,
    backgroundPosition: `${round(pos)}% 0%`,
  };
}

/** The time (s) a strip cell stands for: the middle of its slice of the clip. */
export function cellTime(cell: number, seconds: number, cells = STRIP_FRAMES): number {
  if (!(seconds > 0)) return 0;
  return ((Math.min(cells - 1, Math.max(0, cell)) + 0.5) / cells) * seconds;
}

function round(n: number): number {
  return Math.round(n * 1000) / 1000;
}
