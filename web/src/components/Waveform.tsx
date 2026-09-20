// Phase 9b: the waveform lane under the timeline's clips. Each clip draws its
// own audio (`take.audio`, over the part the cut plays), or, with Play all on
// the recording, its slice of the episode's `track` (its dialogue window, trims
// applied). Peaks come from GET /h3pipe/peaks at the zoom's resolution, through
// a shared cache; a clip only asks once it scrolls into view.

import { memo, useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from "react";
import { peaksCache } from "../cutActions";
import { binsFor, drawPeaks, peaksKey, type PeaksEntry, type PeaksQuery } from "../lib/peaks";
import type { PlayItem } from "../lib/playlist";
import { trackSlice, trackState } from "../lib/recording";
import { useApp } from "../store";
import type { EpisodeStatus, ShotStatus, TakeSummary } from "../types";

/** a stable empty list, so a canvas with nothing to draw doesn't re-render */
const EMPTY: number[] = [];

/** What a clip's lane draws: the query, and what it is (for the tooltip and colour). */
export function waveSource(
  ep: string, item: PlayItem | undefined, take: TakeSummary | undefined, st: EpisodeStatus, recording: boolean, zoom: number,
): { q: PeaksQuery; kind: "take" | "recording"; label: string } | null {
  if (!item) return null;
  const fps = st.fps || 24;
  const ts = trackState(st.track);
  if (recording && ts && !ts.why) {
    const slice = trackSlice(item, fps);
    if (slice) {
      return {
        q: { ep, path: ts.path, bins: binsFor(slice.end - slice.start, zoom), start: slice.start, end: slice.end },
        kind: "recording",
        label: `recording ${slice.start.toFixed(2)}–${slice.end.toFixed(2)} s`,
      };
    }
  }
  const path = take?.audio;
  if (!path || !item.mp4) return null;
  return {
    q: { ep, path, bins: binsFor(item.outT - item.inT, zoom), start: item.inT, end: item.outT, version: take?.finished ?? null },
    kind: "take",
    label: `${path.split("/").pop()} ${item.inT.toFixed(2)}–${item.outT.toFixed(2)} s`,
  };
}

// ComfyUI switches palettes by changing classes and CSS variables on <html> /
// <body>: a canvas has to redraw in the new colours, so lanes watch for that.
let themeN = 0;
const themeSubs = new Set<() => void>();
let themeObserver: MutationObserver | null = null;

function subscribeTheme(fn: () => void): () => void {
  themeSubs.add(fn);
  if (!themeObserver && typeof MutationObserver !== "undefined" && typeof document !== "undefined") {
    themeObserver = new MutationObserver(() => {
      themeN++;
      themeSubs.forEach((f) => f());
    });
    const opts = { attributes: true, attributeFilter: ["class", "style", "data-theme"] };
    themeObserver.observe(document.documentElement, opts);
    if (document.body) themeObserver.observe(document.body, opts);
  }
  return () => {
    themeSubs.delete(fn);
    if (!themeSubs.size && themeObserver) {
      themeObserver.disconnect();
      themeObserver = null;
    }
  };
}

export function useThemeN(): number {
  return useSyncExternalStore(subscribeTheme, () => themeN, () => themeN);
}

/**
 * Peaks for one query, from the shared cache (Phase 9c: the voice refs draw
 * their candidates with this too). `enabled` false holds the request back
 * until the box is on screen.
 */
export function usePeaks(q: PeaksQuery | null, enabled = true): PeaksEntry | undefined {
  const key = q ? peaksKey(q) : "";
  const [entry, setEntry] = useState<PeaksEntry | undefined>(() => (q ? peaksCache.peek(q) : undefined));
  useEffect(() => {
    if (!q) return setEntry(undefined);
    const hit = peaksCache.peek(q);
    setEntry(hit);
    if (hit || !enabled) return;
    let live = true;
    void peaksCache.get(q).then((e) => live && setEntry(e));
    return () => {
      live = false;
    };
    // (the key says everything the query does)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, enabled]);
  return entry;
}

/**
 * A canvas of peaks (0..255), themed and device-pixel-ratio aware. The
 * timeline's lane and the voice refs' players both draw with it.
 */
export const PeaksCanvas = memo(function PeaksCanvas({ peaks, width, height, kind = "take", step = 1, className }: {
  peaks: ArrayLike<number>;
  width: number;
  height: number;
  /** which themed colour: a clip's own sound, or the recording's */
  kind?: "take" | "recording";
  step?: number;
  className?: string;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const theme = useThemeN();
  useLayoutEffect(() => {
    const c = canvas.current;
    if (!c) return;
    const dpr = typeof window !== "undefined" ? window.devicePixelRatio || 1 : 1;
    const w = Math.max(1, Math.round(width));
    c.width = Math.round(w * dpr);
    c.height = Math.round(height * dpr);
    const ctx = c.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    // themed: ComfyUI's variables, through the editor's (resolved on the element)
    const css = getComputedStyle(c);
    const color = (kind === "recording" ? css.getPropertyValue("--h3-wave-rec") : css.getPropertyValue("--h3-wave")).trim() || "#8a8a8a";
    drawPeaks(ctx, peaks, w, height, color, step);
  }, [peaks, width, height, kind, step, theme]);
  return <canvas ref={canvas} className={className} style={{ width, height }} />;
});

/** Starts false; true once the element has been on screen (IntersectionObserver; always true without one). */
export function useSeen(ref: React.RefObject<HTMLElement>): boolean {
  const [seen, setSeen] = useState(typeof IntersectionObserver === "undefined");
  useEffect(() => {
    const el = ref.current;
    if (seen || !el || typeof IntersectionObserver === "undefined") return;
    const io = new IntersectionObserver((es) => {
      if (es.some((e) => e.isIntersecting)) {
        setSeen(true);
        io.disconnect();
      }
    }, { rootMargin: "0px 400px" });
    io.observe(el);
    return () => io.disconnect();
  }, [ref, seen]);
  return seen;
}

export const WaveLane = memo(function WaveLane({ ep, s, item, take, st, width, height, zoom }: {
  ep: string; s: ShotStatus; item: PlayItem | undefined; take: TakeSummary | undefined; st: EpisodeStatus;
  width: number; height: number; zoom: number;
}) {
  const recording = useApp((x) => x.cutAudio === "recording");
  const box = useRef<HTMLDivElement>(null);
  const seen = useSeen(box);
  const src = waveSource(ep, item, take, st, recording, zoom);
  const entry = usePeaks(src?.q ?? null, seen);

  const title = !src
    ? item ? (take ? `${s.shot}: no audio for this take` : `${s.shot}: no take`) : `${s.shot}: not in Play all`
    : entry && !entry.ok ? `${s.shot}: ${entry.error}`
      : entry?.ok && entry.data.silent ? `${s.shot}: ${src.label} · no audio stream`
        : `${s.shot}: ${src.label}`;
  return (
    <div ref={box} className={`h3-wave${src?.kind === "recording" ? " h3-wave-rec" : ""}`} style={{ width, height }} title={title}>
      {src && <PeaksCanvas peaks={entry?.ok ? entry.data.peaks : EMPTY} width={width} height={height} kind={src.kind} />}
      {src && !entry && seen && <span className="h3-wave-note">…</span>}
      {entry && !entry.ok && <span className="h3-wave-note h3-err">!</span>}
    </div>
  );
});
