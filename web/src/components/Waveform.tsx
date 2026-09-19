// Phase 9b: the waveform lane under the timeline's clips. Each clip draws its
// own audio (`take.audio`, over the part the cut plays), or, with Play all on
// the recording, its slice of the episode's `track` (its dialogue window, trims
// applied). Peaks come from GET /h3pipe/peaks at the zoom's resolution, through
// a shared cache; a clip only asks once it scrolls into view.

import { memo, useEffect, useLayoutEffect, useRef, useState } from "react";
import { peaksCache } from "../cutActions";
import { binsFor, drawPeaks, type PeaksEntry, type PeaksQuery } from "../lib/peaks";
import type { PlayItem } from "../lib/playlist";
import { trackSlice } from "../lib/recording";
import { useApp } from "../store";
import type { EpisodeStatus, ShotStatus, TakeSummary } from "../types";

/** What a clip's lane draws: the query, and what it is (for the tooltip and colour). */
export function waveSource(
  ep: string, item: PlayItem | undefined, take: TakeSummary | undefined, st: EpisodeStatus, recording: boolean, zoom: number,
): { q: PeaksQuery; kind: "take" | "recording"; label: string } | null {
  if (!item) return null;
  const fps = st.fps || 24;
  if (recording && st.track?.path) {
    const slice = trackSlice(item, fps);
    if (slice) {
      return {
        q: { ep, path: st.track.path, bins: binsFor(slice.end - slice.start, zoom), start: slice.start, end: slice.end },
        kind: "recording",
        label: `recording ${slice.start.toFixed(2)}–${slice.end.toFixed(2)} s`,
      };
    }
  }
  const path = take?.audio;
  if (!path || !item.mp4) return null;
  return {
    q: { ep, path, bins: binsFor(item.outT - item.inT, zoom), start: item.inT, end: item.outT },
    kind: "take",
    label: `${path.split("/").pop()} ${item.inT.toFixed(2)}–${item.outT.toFixed(2)} s`,
  };
}

/** Starts false; true once the element has been on screen (IntersectionObserver; always true without one). */
function useSeen(ref: React.RefObject<HTMLElement>): boolean {
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
  const canvas = useRef<HTMLCanvasElement>(null);
  const seen = useSeen(box);
  const src = waveSource(ep, item, take, st, recording, zoom);
  const key = src ? `${src.q.path}|${src.q.start}|${src.q.end}|${src.q.bins}` : "";
  const [entry, setEntry] = useState<PeaksEntry | undefined>(() => (src ? peaksCache.peek(src.q) : undefined));

  useEffect(() => {
    if (!src) return setEntry(undefined);
    const hit = peaksCache.peek(src.q);
    setEntry(hit);
    if (hit || !seen) return;
    let live = true;
    void peaksCache.get(src.q).then((e) => live && setEntry(e));
    return () => {
      live = false;
    };
    // (the key says everything the query does)
  }, [key, seen]);

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
    const color = (src?.kind === "recording" ? css.getPropertyValue("--h3-wave-rec") : css.getPropertyValue("--h3-wave")).trim() || "#8a8a8a";
    const peaks = entry?.ok ? entry.data.peaks : [];
    if (entry?.ok) drawPeaks(ctx, peaks, w, height, color, 1);
    else ctx.clearRect(0, 0, w, height);
  }, [entry, width, height, src?.kind]);

  const title = !src
    ? item ? (take ? `${s.shot}: no audio for this take` : `${s.shot}: no take`) : `${s.shot}: not in Play all`
    : entry && !entry.ok ? `${s.shot}: ${entry.error}`
      : entry?.ok && entry.data.silent ? `${s.shot}: ${src.label} · no audio stream`
        : `${s.shot}: ${src.label}`;
  return (
    <div ref={box} className={`h3-wave${src?.kind === "recording" ? " h3-wave-rec" : ""}`} style={{ width, height }} title={title}>
      {src && <canvas ref={canvas} style={{ width, height }} />}
      {src && !entry && seen && <span className="h3-wave-note">…</span>}
      {entry && !entry.ok && <span className="h3-wave-note h3-err">!</span>}
    </div>
  );
});
