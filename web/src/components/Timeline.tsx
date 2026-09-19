import {
  memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState,
  type KeyboardEvent as RKeyboardEvent, type PointerEvent as RPointerEvent, type RefObject,
} from "react";
import {
  assemble, currentPlaylist, openInspector, openMenu, openViewer, playAll, refreshEpisode, seekCut, seekToShot, select,
  setZoom, showMissingRefs, toggleCutPlay,
} from "../actions";
import { cutKey, moveClip, redoCut, seekCutAt, setTrims, toggleWaves, undoCut } from "../cutActions";
import { api, host } from "../host";
import { clampTrim, dropIndex, framesLabel, pxToFrames } from "../lib/cutEdit";
import {
  ESTIMATE_TITLE, fmtClock, fmtSeconds, fmtShotSeconds, groupBySequence, lengthEstimated, shotBadges, shotSeconds, tn,
} from "../lib/format";
import { missingRefsSummary } from "../lib/missingRefs";
import { clipTake, locate, totalDuration, type PlayItem } from "../lib/playlist";
import { targetBadges } from "../lib/targets";
import { ZOOM_MAX, ZOOM_MIN, renderingTakes, statusKey, store, useApp } from "../store";
import type { EpisodeStatus, Pass, ShotStatus, TakeSummary, TargetList } from "../types";
import { aspectOf, useSize, useStatus } from "./hooks";
import { PassToggle } from "./ShotsTab";
import { useTargets } from "./Targets";
import { Badges, Progress, mediaStyle, useScrub } from "./Thumb";
import { WaveLane } from "./Waveform";

const MIN_CLIP = 26;
/** the ruler band over each clip */
const RULER_H = 12;
/** the waveform lane under each clip */
export const WAVE_H = 28;

/** A clip being dragged to a new place: the insertion point and where its marker goes. */
interface DragState {
  shot: string;
  gap: number | null;
  /** the marker's x in the track's coordinates */
  x: number;
}

/** An edge being dragged: the trims it would set, and the pointer (for the tooltip). */
export interface TrimPreview {
  shot: string;
  side: "in" | "out";
  trimIn: number;
  trimOut: number;
  px: number;
  py: number;
}

/** The Play all playhead: a line over the clip playing, at its offset. While
 * playing, the track scrolls to keep it in view. */
function Playhead({ trackRef, wrapRef, zoom }: { trackRef: RefObject<HTMLDivElement>; wrapRef: RefObject<HTMLDivElement>; zoom: number }) {
  const on = useApp((s) => s.viewer?.kind === "cut");
  const pos = useApp((s) => s.cutPlay.pos);
  const playing = useApp((s) => s.viewer?.kind === "cut" && s.cutPlay.playing);
  const items = useApp((s) => currentPlaylist(s));
  const [left, setLeft] = useState<number | null>(null);
  useLayoutEffect(() => {
    const track = trackRef.current;
    if (!on || !track) return setLeft(null);
    const { index, offset } = locate(items, pos);
    const it = items[index];
    const el = it ? track.querySelector<HTMLElement>(`[data-shot="${CSS.escape(it.shot)}"]`) : null;
    if (!it || !el) return setLeft(null);
    const tr = track.getBoundingClientRect();
    const r = el.getBoundingClientRect();
    const x = r.left - tr.left + (it.dur > 0 ? (offset / it.dur) * r.width : 0);
    setLeft(x);
    // follow: keep the playhead in view while it plays
    const wrap = wrapRef.current;
    if (playing && wrap) {
      const cx = x + track.offsetLeft;
      if (cx < wrap.scrollLeft + 16 || cx > wrap.scrollLeft + wrap.clientWidth - 32) {
        wrap.scrollLeft = Math.max(0, cx - wrap.clientWidth * 0.25);
      }
    }
  }, [on, pos, items, zoom, trackRef, wrapRef, playing]);
  if (left == null) return null;
  return <div className="h3-playhead" style={{ left }} />;
}

/** The ruler over one clip: a tick per second of the cut, its start time, click or drag to seek. */
const Ruler = memo(function Ruler({ item, width, zoom, onDown }: {
  item: PlayItem | undefined; width: number; zoom: number; onDown: (e: RPointerEvent<HTMLDivElement>) => void;
}) {
  if (!item) return <div className="h3-ruler h3-ruler-off" style={{ width, height: RULER_H }} />;
  const scale = item.dur > 0 ? width / item.dur : zoom;
  // ticks on whole seconds of the cut's clock (every 5 s when zoomed far out)
  const every = scale < 12 ? 5 : 1;
  const first = Math.ceil(item.start / every) * every - item.start;
  const period = every * scale;
  const style = {
    width, height: RULER_H,
    backgroundImage: period >= 3 ? "linear-gradient(to right, var(--h3-muted) 1px, transparent 1px)" : undefined,
    backgroundSize: period >= 3 ? `${period}px 5px` : undefined,
    backgroundPosition: `${first * scale}px 100%`,
    backgroundRepeat: "repeat-x",
  };
  return (
    <div
      className="h3-ruler"
      style={style}
      data-ruler={item.shot}
      data-start={item.start}
      data-dur={item.dur}
      title={`${fmtClock(item.start)} · click or drag to seek (opens Play all paused)`}
      onPointerDown={onDown}
    >
      {width > 44 && <span>{fmtClock(item.start)}</span>}
    </div>
  );
});

/** While an edge is dragged: the whole take as a filmstrip, the trimmed parts dimmed. */
function TrimGhost({ ep, take, item, preview, zoom, fps, height }: {
  ep: string; take: TakeSummary | undefined; item: PlayItem; preview: TrimPreview; zoom: number; fps: number; height: number;
}) {
  const total = item.total ?? 0;
  if (!(total > 0)) return null;
  const px = (frames: number) => (frames / fps) * zoom;
  const full = px(total);
  const head = px(preview.trimIn);
  const tail = px(preview.trimOut);
  const strip = take?.strip ? api().fileUrl(ep, take.strip) : take?.thumb ? api().fileUrl(ep, take.thumb) : null;
  return (
    <div
      className="h3-trim-ghost"
      style={{
        left: -head, width: full, top: RULER_H, height,
        backgroundImage: strip ? `url("${strip}")` : undefined,
        backgroundSize: take?.strip ? `${full}px 100%` : "auto 100%",
      }}
    >
      <div className="h3-trim-dim" style={{ left: 0, width: head }} />
      <div className="h3-trim-keep" style={{ left: head, width: Math.max(1, full - head - tail) }} />
      <div className="h3-trim-dim" style={{ right: 0, width: tail }} />
    </div>
  );
}

const Clip = memo(function Clip({
  ep, pass, s, other, width, height, aspect, selected, rendering, progress, targets, seriesDefault, fps, trimmable, dragging,
  onBodyDown, onEdgeDown, suppress,
}: {
  ep: string; pass: Pass; s: ShotStatus; other: EpisodeStatus | undefined; width: number; height: number;
  aspect: number; selected: boolean; rendering: Set<number>; progress?: { value: number; max: number };
  targets: TargetList | null; seriesDefault: string; fps: number; trimmable: boolean; dragging: boolean;
  onBodyDown: (e: RPointerEvent<HTMLDivElement>, s: ShotStatus) => void;
  onEdgeDown: (e: RPointerEvent<HTMLDivElement>, s: ShotStatus, side: "in" | "out") => void;
  suppress: RefObject<boolean>;
}) {
  const [cell, scrub] = useScrub();
  const take = clipTake(s, other);
  const takePass: Pass = s.cut.placeholder ? s.cut.pass : pass;
  const secs = shotSeconds(s, fps);
  const mediaW = Math.min(width, Math.round(height * aspect));
  const locked = !!s.cut.locked;
  const trimIn = s.cut.trim_in || 0;
  const trimOut = s.cut.trim_out || 0;
  // a placeholder's take is the other pass's: its target is compared all the same
  const badges = useMemo(
    () => [...targetBadges(s, targets, seriesDefault, take), ...shotBadges(s, rendering)].filter((b) => b.kind !== "locked" && b.kind !== "trimmed"),
    [s, rendering, targets, seriesDefault, take],
  );
  const cls = [
    "h3-clip", selected && "h3-sel", s.cut.placeholder && "h3-placeholder", s.orphan && "h3-orphan", locked && "h3-locked",
    dragging && "h3-dragging",
  ].filter(Boolean).join(" ");
  const title = [
    `${s.shot} · ${fmtShotSeconds(s, secs)}${s.size ? ` · ${s.size}` : ""}${lengthEstimated(s) ? `\n≈ ${ESTIMATE_TITLE}` : ""}`,
    take ? `${s.cut.placeholder ? `${s.cut.pass} ` : ""}${tn(take.take)} (${take.status})` : "no take in the cut",
    trimIn || trimOut ? `trimmed: ${framesLabel(trimIn, fps)} off the head, ${framesLabel(trimOut, fps)} off the tail` : "",
    locked ? "locked: it can't be moved, trimmed or re-picked (unlock from its menu)" : "",
    ...badges.map((b) => b.title),
    `click: select (jumps there while playing all) · double-click: viewer · right-click: menu${locked ? "" : " · drag: move · drag an edge: trim"}`,
  ].filter(Boolean).join("\n");
  return (
    <div
      className={cls}
      style={{ width }}
      title={title}
      onPointerDown={(e) => onBodyDown(e, s)}
      onClick={() => {
        if (suppress.current) return;
        // while Play all is open, a click jumps the player there
        seekToShot(s.shot);
        select(s.shot, take && !s.cut.placeholder ? take.take : null);
      }}
      onDoubleClick={() => take?.mp4 && openViewer(s.shot, take.take, null, "single", takePass)}
      onContextMenu={(e) => {
        e.preventDefault();
        openMenu(e.clientX, e.clientY, s.shot, take?.take ?? null, takePass);
      }}
      {...scrub}
    >
      <div
        className={`h3-clip-media${take?.thumb || take?.strip ? "" : " h3-thumb h3-empty"}`}
        style={{ width: mediaW, ...mediaStyle(ep, take, cell) }}
      >
        {!(take?.thumb || take?.strip) && width > 40 && <span>{take ? (take.status === "ok" ? "no thumb" : take.status) : "no take"}</span>}
      </div>
      <div className="h3-clip-fill" />
      {trimIn > 0 && <div className="h3-trimmed-mark h3-l" title={`${framesLabel(trimIn, fps)} trimmed off the head`} />}
      {trimOut > 0 && <div className="h3-trimmed-mark h3-r" title={`${framesLabel(trimOut, fps)} trimmed off the tail`} />}
      {height > 36 && badges.length > 0 && (
        <div className="h3-clip-top">
          <Badges badges={badges} max={width < 90 ? 1 : 3} />
        </div>
      )}
      <div className="h3-clip-bottom">
        {locked && <i className="pi pi-lock" title="Locked in the cut" />}
        <b>{s.shot}</b>
        {lengthEstimated(s) && <span className="h3-est" title={ESTIMATE_TITLE}>≈</span>}
        {take && width > 60 && <span>{tn(take.take)}</span>}
      </div>
      {progress && <Progress value={progress.value} max={progress.max} />}
      {trimmable && !locked && (
        <>
          <div className="h3-trim-h h3-l" title="Drag to trim the head (frame by frame)" onPointerDown={(e) => onEdgeDown(e, s, "in")} onClick={(e) => e.stopPropagation()} />
          <div className="h3-trim-h h3-r" title="Drag to trim the tail (frame by frame)" onPointerDown={(e) => onEdgeDown(e, s, "out")} onClick={(e) => e.stopPropagation()} />
        </>
      )}
    </div>
  );
});

/** The Cut menu button (Reset order, Clear trims, Copy from the other pass, undo / redo). */
function CutMenuButton() {
  const open = useApp((s) => !!s.cutMenu);
  return (
    <button
      data-cutmenu-btn=""
      className={`h3-btn${open ? " h3-on" : ""}`}
      title="Cut: reset the order, clear trims, copy from the other pass, undo / redo"
      onClick={(e) => {
        const r = e.currentTarget.getBoundingClientRect();
        store.set({ cutMenu: store.get().cutMenu ? null : { x: r.left, y: r.bottom + 2 } });
      }}
    >
      <i className="pi pi-sort-alt" /> Cut <i className="pi pi-angle-down" style={{ fontSize: 9 }} />
    </button>
  );
}

export function Timeline() {
  const ep = useApp((s) => s.ep);
  const pass = useApp((s) => s.pass);
  const zoom = useApp((s) => s.zoom);
  const shot = useApp((s) => s.shot);
  const running = useApp((s) => s.running);
  const prompts = useApp((s) => s.prompts);
  const progress = useApp((s) => s.progress);
  const asm = useApp((s) => s.assemble);
  const waves = useApp((s) => s.waves);
  const undo = useApp((s) => (s.ep ? s.cutUndo[statusKey(s.ep, s.pass)] : undefined));
  const err = useApp((s) => (s.ep ? s.statusError[statusKey(s.ep, s.pass)] : undefined));
  const playing = useApp((s) => s.viewer?.kind === "cut" && s.cutPlay.playing);
  const playerOpen = useApp((s) => s.viewer?.kind === "cut");
  const pos = useApp((s) => (s.viewer?.kind === "cut" ? s.cutPlay.pos : null));
  const items = useApp((s) => currentPlaylist(s));
  const st = useStatus();
  const other = useStatus(pass === "proxy" ? "final" : "proxy");
  const [trackRef, size] = useSize<HTMLDivElement>();
  const wrapRef = useRef<HTMLDivElement>(null);
  const surfaceRef = useRef<HTMLDivElement>(null);
  const innerRef = useRef<HTMLDivElement>(null);
  const suppress = useRef(false);
  const [drag, setDrag] = useState<DragState | null>(null);
  const [trim, setTrim] = useState<TrimPreview | null>(null);
  const groups = useMemo(() => groupBySequence(st?.shots ?? [], st?.fps), [st]);
  const itemBy = useMemo(() => new Map(items.map((i) => [i.shot, i])), [items]);
  const { list: targets, seriesDefault } = useTargets();
  const fps = st?.fps || 24;
  // clip height: the track minus padding, the sequence label, the ruler and the waveform lane
  const clipH = Math.max(24, (size.height || 150) - 6 - 16 - RULER_H - (waves ? WAVE_H + 2 : 0) - 2);
  const aspect = aspectOf(st);
  const total = items.length ? totalDuration(items) : st?.shots.reduce((n, s) => n + (shotSeconds(s, st.fps) ?? 0), 0) ?? 0;
  const missing = useMemo(() => missingRefsSummary(st?.shots ?? []), [st]);
  const moved = useMemo(() => (st?.shots ?? []).filter((s) => s.cut?.out_of_order && !s.orphan).length, [st]);

  /** a clip's width: its length in the cut (trims applied; the edge being dragged, previewed) */
  const widthOf = useCallback((s: ShotStatus): number => {
    const it = itemBy.get(s.shot);
    let secs = it ? it.dur : shotSeconds(s, fps) ?? 1;
    if (trim && trim.shot === s.shot && it?.total != null) secs = Math.max(1, it.total - trim.trimIn - trim.trimOut) / fps;
    return Math.max(MIN_CLIP, Math.round(secs * zoom));
  }, [itemBy, trim, fps, zoom]);

  // keep the selected clip in view
  useEffect(() => {
    if (!shot || !wrapRef.current) return;
    const el = wrapRef.current.querySelector<HTMLElement>(`[data-shot="${CSS.escape(shot)}"]`);
    el?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [shot]);

  // the cut's keys, while the timeline has focus (stopped here, so ComfyUI's
  // canvas doesn't also take space or Ctrl+Z)
  const onKeyDown = (e: RKeyboardEvent<HTMLDivElement>) => {
    const tag = (e.target as HTMLElement).tagName;
    if (tag === "BUTTON" && (e.key === " " || e.key === "Enter")) return;
    if (cutKey(e)) {
      e.preventDefault();
      e.stopPropagation();
    }
  };

  // ctrl/cmd + wheel zooms
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      e.preventDefault();
      setZoom(Math.round(Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, zoom * (e.deltaY < 0 ? 1.15 : 1 / 1.15)))));
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [zoom]);

  const focusSurface = () => surfaceRef.current?.focus({ preventScroll: true });

  /** Pointer drags: window listeners until the button comes up (or Esc). */
  const track = (onMove: (e: PointerEvent) => void, onUp: (cancelled: boolean) => void) => {
    const stop = (cancelled: boolean) => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", cancel);
      window.removeEventListener("keydown", key, true);
      onUp(cancelled);
    };
    const up = () => stop(false);
    const cancel = () => stop(true);
    const key = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.preventDefault();
      e.stopPropagation();
      stop(true);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", cancel);
    window.addEventListener("keydown", key, true);
  };

  /** Swallow the click that ends a drag. */
  const endDrag = () => {
    suppress.current = true;
    setTimeout(() => (suppress.current = false), 0);
  };

  // ---- reorder: drag a clip between clips ----
  const onBodyDown = useCallback((e: RPointerEvent<HTMLDivElement>, s: ShotStatus) => {
    if (e.button !== 0 || e.pointerType === "touch") return;
    focusSurface();
    const x0 = e.clientX;
    const y0 = e.clientY;
    let active = false;
    let gap: number | null = null;
    track((ev) => {
      if (!active) {
        if (Math.hypot(ev.clientX - x0, ev.clientY - y0) < 6) return;
        active = true;
        if (s.cut.locked) {
          host().toast("warn", `${s.shot} is locked`, "A locked clip can't be moved. Unlock it from its menu first.");
          gap = null;
          return;
        }
      }
      if (s.cut.locked) return;
      const inner = innerRef.current;
      const wrap = wrapRef.current;
      if (!inner) return;
      const els = [...inner.querySelectorAll<HTMLElement>("[data-shot]")];
      const edges = els.map((el) => el.getBoundingClientRect());
      gap = dropIndex(edges, ev.clientX);
      const ir = inner.getBoundingClientRect();
      const at = !edges.length ? 0 : gap === 0 ? edges[0].left : gap === edges.length ? edges[gap - 1].right : (edges[gap - 1].right + edges[gap].left) / 2;
      setDrag({ shot: s.shot, gap, x: at - ir.left });
      // scroll when held near either end
      if (wrap) {
        const wr = wrap.getBoundingClientRect();
        if (ev.clientX < wr.left + 30) wrap.scrollLeft -= 12;
        else if (ev.clientX > wr.right - 30) wrap.scrollLeft += 12;
      }
    }, (cancelled) => {
      setDrag(null);
      if (!active) return;
      endDrag();
      if (!cancelled && gap != null && !s.cut.locked) void moveClip(s.shot, gap);
    });
  }, []);

  // ---- trims: drag a clip's edge ----
  const onEdgeDown = useCallback((e: RPointerEvent<HTMLDivElement>, s: ShotStatus, side: "in" | "out") => {
    if (e.button !== 0) return;
    e.stopPropagation();
    e.preventDefault();
    focusSurface();
    const it = currentPlaylist().find((x) => x.shot === s.shot);
    const f = store.get().status[statusKey(store.get().ep ?? "", store.get().pass)]?.fps || 24;
    const z = store.get().zoom;
    if (!it || it.total == null) return;
    const total = it.total;
    const a0 = it.trimIn;
    const b0 = it.trimOut;
    const x0 = e.clientX;
    let a = a0;
    let b = b0;
    const place = (ev: { clientX: number; clientY: number }) => {
      const d = pxToFrames(ev.clientX - x0, z, f);
      if (side === "in") a = clampTrim("in", a0 + d, total, 0, b0);
      else b = clampTrim("out", b0 - d, total, a0, 0);
      setTrim({ shot: s.shot, side, trimIn: a, trimOut: b, px: ev.clientX, py: ev.clientY });
    };
    place(e);
    track(place, (cancelled) => {
      setTrim(null);
      endDrag();
      if (!cancelled && (a !== a0 || b !== b0)) void setTrims(s.shot, a, b, `Trim ${s.shot} ${side === "in" ? "head" : "tail"}`);
    });
  }, []);

  // ---- the ruler: click or drag to seek ----
  const rulerTime = (clientX: number): number => {
    const inner = innerRef.current;
    const bands = inner ? [...inner.querySelectorAll<HTMLElement>("[data-ruler]")] : [];
    if (!bands.length) return 0;
    for (const b of bands) {
      const r = b.getBoundingClientRect();
      const start = Number(b.dataset.start) || 0;
      const dur = Number(b.dataset.dur) || 0;
      if (clientX < r.left) return start;
      if (clientX <= r.right) return start + (r.width > 0 ? ((clientX - r.left) / r.width) * dur : 0);
    }
    const last = bands[bands.length - 1];
    return (Number(last.dataset.start) || 0) + (Number(last.dataset.dur) || 0);
  };
  const onRulerDown = useCallback((e: RPointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    e.stopPropagation();
    e.preventDefault();
    focusSurface();
    seekCutAt(rulerTime(e.clientX));
    track((ev) => seekCut(rulerTime(ev.clientX)), () => {});
  }, []);

  const dragged = drag?.shot;
  const trimItem = trim ? itemBy.get(trim.shot) : undefined;
  const trimShot = trim ? st?.shots.find((x) => x.shot === trim.shot) : undefined;

  return (
    <div className="h3-surface h3-timeline" tabIndex={-1} ref={surfaceRef} onKeyDown={onKeyDown} style={{ outline: "none" }}>
      <div className="h3-bar">
        <span className="h3-title h3-ell" title={ep ?? ""}>{st ? `${st.episode}` : "Timeline"}</span>
        {st && <span className="h3-muted h3-small">{st.shots.length} shots · {fmtSeconds(total)}</span>}
        <button
          className={`h3-btn${playing ? " h3-on" : ""}`}
          disabled={!st?.shots.length}
          title="Play all: the cut in order from its takes, in the viewer (space: play / pause · J / K / L: back / stop / forward · click a clip to jump · click the ruler to seek)"
          onClick={() => (playerOpen ? toggleCutPlay() : playAll())}
        >
          <i className={playing ? "pi pi-pause" : "pi pi-play"} /> {playing ? "Pause" : playerOpen ? "Play" : "Play all"}
        </button>
        {pos != null && <span className="h3-mono h3-muted">{fmtClock(pos)}</span>}
        <PassToggle />
        <CutMenuButton />
        <span className="h3-seg">
          <button disabled={!undo?.undo} title={undo?.undo ? `Undo: ${undo.undo} (Ctrl+Z)` : "Nothing to undo (Ctrl+Z)"} onClick={() => void undoCut()}><i className="pi pi-undo" /></button>
          <button disabled={!undo?.redo} title={undo?.redo ? `Redo: ${undo.redo} (Ctrl+Shift+Z)` : "Nothing to redo (Ctrl+Shift+Z)"} onClick={() => void redoCut()}><i className="pi pi-refresh" style={{ transform: "scaleX(-1)" }} /></button>
        </span>
        <button className={`h3-btn h3-icon${waves ? " h3-on" : ""}`} title={waves ? "Hide the waveforms" : "Show each clip's waveform (or the recording's, with Play all's audio on the recording)"} onClick={() => toggleWaves()}>
          <i className="pi pi-wave-pulse" />
        </button>
        {moved > 0 && (
          <span className="h3-badge h3-b-moved" title={`${moved} shot${moved > 1 ? "s are" : " is"} out of script order (Cut ▸ Reset order puts them back)`}>
            {moved} moved
          </span>
        )}
        {missing.shots.length > 0 && (
          <button className="h3-badge h3-b-missing-refs h3-badge-btn" title={`${missing.text}\n${missing.files.map((f) => f.path).join("\n")}\nClick: show them in the Refs tab`} onClick={() => showMissingRefs(null)}>
            {missing.shots.length} missing refs
          </button>
        )}
        <span className="h3-row" title="Zoom (ctrl + wheel over the track)">
          <i className="pi pi-search-minus h3-muted" />
          <input type="range" min={ZOOM_MIN} max={ZOOM_MAX} value={zoom} onChange={(e) => setZoom(Number(e.target.value))} style={{ width: 90 }} />
          <i className="pi pi-search-plus h3-muted" />
        </span>
        <span className="h3-grow" />
        <button className="h3-btn" disabled={!ep || asm.busy} title={`Export: assemble the ${pass} review cut into one mp4 with ffmpeg (missing shots are skipped). Not needed to watch the cut: use Play all.`} onClick={() => void assemble(true)}>
          <i className={asm.busy ? "pi pi-spin pi-spinner" : "pi pi-download"} /> {asm.busy ? "Assembling…" : "Export"}
        </button>
        <button className="h3-btn h3-icon" title="Inspect the selected shot" onClick={() => openInspector()}><i className="pi pi-sliders-h" /></button>
        <button className="h3-btn h3-icon" title="Open the Shots tab" onClick={() => host().show("shots")}><i className="pi pi-list" /></button>
        <button className="h3-btn h3-icon" title="Refresh" disabled={!ep} onClick={() => void refreshEpisode()}><i className="pi pi-refresh" /></button>
      </div>
      <div className="h3-track-wrap" ref={wrapRef}>
        <div ref={trackRef} style={{ position: "absolute", inset: 0, pointerEvents: "none" }} />
        {!ep && <div className="h3-empty-state">Pick an episode in the h3 Shots tab.</div>}
        {ep && err && !st && <div className="h3-pad"><div className="h3-note h3-note-err">{err}</div></div>}
        {ep && st && (
          <div className={`h3-track${drag ? " h3-track-dragging" : ""}`} ref={innerRef}>
            {groups.map((g) => (
              <div key={`${g.sequence}@${g.start}`} className="h3-tl-seq">
                <div className="h3-tl-seq-label" title={`${g.sequence} · ${fmtSeconds(g.seconds)}`}>{g.sequence}</div>
                <div className="h3-tl-clips">
                  {g.shots.map((s) => {
                    const r = renderingTakes({ running, prompts }, ep, pass, s.shot);
                    const it = itemBy.get(s.shot);
                    const width = widthOf(s);
                    const take = clipTake(s, s.cut.placeholder ? other : undefined);
                    return (
                      <div key={s.shot} data-shot={s.shot} className="h3-tl-col" style={{ width }}>
                        <Ruler item={it} width={width} zoom={zoom} onDown={onRulerDown} />
                        <div style={{ height: clipH, position: "relative" }}>
                          <Clip
                            ep={ep}
                            pass={pass}
                            s={s}
                            other={s.cut.placeholder ? other : undefined}
                            width={width}
                            height={clipH}
                            aspect={aspect}
                            selected={shot === s.shot}
                            rendering={r.size ? r : NO_TAKES}
                            progress={r.size && running ? progress[running] : undefined}
                            targets={targets}
                            seriesDefault={seriesDefault}
                            fps={fps}
                            trimmable={!!it && it.total != null && !s.orphan}
                            dragging={dragged === s.shot}
                            onBodyDown={onBodyDown}
                            onEdgeDown={onEdgeDown}
                            suppress={suppress}
                          />
                        </div>
                        {trim && trim.shot === s.shot && trimItem && (
                          <TrimGhost ep={ep} take={take} item={trimItem} preview={trim} zoom={zoom} fps={fps} height={clipH} />
                        )}
                        {waves && <WaveLane ep={ep} s={s} item={it} take={take} st={st} width={width} height={WAVE_H} zoom={zoom} />}
                      </div>
                    );
                  })}
                </div>
              </div>
            ))}
            {!st.shots.length && <div className="h3-empty-state">No shots.</div>}
            {drag && drag.gap != null && <div className="h3-drop-marker" style={{ left: drag.x }} />}
            <Playhead trackRef={innerRef} wrapRef={wrapRef} zoom={zoom} />
          </div>
        )}
      </div>
      {trim && trimShot && trimItem && (
        <div className="h3-trim-tip" style={{ left: trim.px + 14, top: trim.py - 34 }}>
          {trim.shot} · {trim.side === "in" ? "head" : "tail"} trim {framesLabel(trim.side === "in" ? trim.trimIn : trim.trimOut, fps)}
          <br />
          <span className="h3-muted">
            clip {fmtSeconds(Math.max(1, (trimItem.total ?? 0) - trim.trimIn - trim.trimOut) / fps)} of {fmtSeconds((trimItem.total ?? 0) / fps)} · Esc cancels
          </span>
        </div>
      )}
    </div>
  );
}

const NO_TAKES: Set<number> = new Set();
