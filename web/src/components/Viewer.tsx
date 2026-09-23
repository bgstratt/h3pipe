// The floating viewer. One window, three kinds (store `viewer.kind`):
//  - takes: one shot's takes with A/B compare (side by side or wipe);
//  - cut:   Play all, the cut in order from the takes themselves (CutPlayer);
//  - image: a ref's candidates (stills), with the same A/B compare.

import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type PointerEvent as RPointerEvent, type ReactNode } from "react";
import {
  closeViewer, currentPlaylist, openInspector, openMenu, openRedo, openSidecar, openViewer, pickRef, pickTake,
  reportCutPos, seekCut, setCutPlaying, updateViewer,
} from "../actions";
import { cutKey, isTyping, lockedPickRefusal, setCutAudio } from "../cutActions";
import { api } from "../host";
import { RECORDING_IGNORES_AUDIO, clipsWithAudio } from "../lib/audioSource";
import { anyOutOfOrder } from "../lib/cutEdit";
import { fmtClock, realStale, tn } from "../lib/format";
import { frameAt } from "../lib/keyframes";
import {
  atOutPoint, baseIn, clipOffset, cutTime, fileTime, locate, nextVideo, totalDuration, type PlayItem,
} from "../lib/playlist";
import { masterSync, needsResync, recordingAt, trackState } from "../lib/recording";
import { takesOf, viewLabel, viewOf } from "../lib/refs";
import { shotTarget, takeTargetBadge, targetLabel } from "../lib/targets";
import { store, useApp, type CompareMode, type ViewerState } from "../store";
import type { RefTake, TakeSummary } from "../types";
import { FloatingWindow, defaultViewerRect } from "./FloatingWindow";
import { aspectOf, useShotStatus, useStatus } from "./hooks";
import { useTargets } from "./Targets";
import { Thumb } from "./Thumb";

// v2: round 2 moved the default place so the viewer and the inspector sit side by side
const RECT_KEY = "h3pipe.viewer.rect.v2";

/** Keep B on A's clock: play/pause/seek/rate follow A. */
function useSync(a: HTMLVideoElement | null, b: HTMLVideoElement | null) {
  useEffect(() => {
    if (!a || !b) return;
    const sync = () => {
      if (Math.abs(b.currentTime - a.currentTime) > 0.05) b.currentTime = Math.min(a.currentTime, b.duration || a.currentTime);
    };
    const play = () => {
      sync();
      void b.play().catch(() => {});
    };
    const pause = () => {
      b.pause();
      b.playbackRate = a.playbackRate;
      sync();
    };
    const rate = () => (b.playbackRate = a.playbackRate);
    let raf = 0;
    // while playing: nudge B's rate to close small gaps (smooth), seek on big ones
    const drift = () => {
      if (!a.paused && !b.paused) {
        const d = a.currentTime - b.currentTime; // > 0: B is behind
        if (Math.abs(d) > 0.25) b.currentTime = a.currentTime;
        else b.playbackRate = a.playbackRate * (1 + Math.max(-0.15, Math.min(0.15, d * 3)));
      }
      raf = requestAnimationFrame(drift);
    };
    raf = requestAnimationFrame(drift);
    a.addEventListener("play", play);
    a.addEventListener("pause", pause);
    a.addEventListener("seeked", sync);
    a.addEventListener("ratechange", rate);
    sync();
    if (!a.paused) play();
    return () => {
      cancelAnimationFrame(raf);
      a.removeEventListener("play", play);
      a.removeEventListener("pause", pause);
      a.removeEventListener("seeked", sync);
      a.removeEventListener("ratechange", rate);
    };
  }, [a, b]);
}

function Transport({ a, fps, audio, setAudio, hasB }: {
  a: HTMLVideoElement | null; fps: number; audio: "a" | "b" | "off"; setAudio: (x: "a" | "b" | "off") => void; hasB: boolean;
}) {
  const [, force] = useState(0);
  const [loop, setLoop] = useState(true);
  useEffect(() => {
    if (!a) return;
    const f = () => force((n) => n + 1);
    const evs = ["timeupdate", "play", "pause", "loadedmetadata", "durationchange", "seeked"];
    evs.forEach((e) => a.addEventListener(e, f));
    return () => evs.forEach((e) => a.removeEventListener(e, f));
  }, [a]);
  useEffect(() => {
    if (a) a.loop = loop;
  }, [a, loop]);
  const t = a?.currentTime ?? 0;
  const d = a && Number.isFinite(a.duration) ? a.duration : 0;
  const step = (n: number) => {
    if (!a) return;
    a.pause();
    a.currentTime = Math.min(d, Math.max(0, a.currentTime + n / (fps || 24)));
  };
  return (
    <div className="h3-transport">
      <button className="h3-btn h3-icon" disabled={!a} title="Previous frame (,)" onClick={() => step(-1)}><i className="pi pi-step-backward" /></button>
      <button className="h3-btn h3-icon" disabled={!a} title="Play / pause (space)" onClick={() => (a?.paused ? void a.play() : a?.pause())}>
        <i className={a && !a.paused ? "pi pi-pause" : "pi pi-play"} />
      </button>
      <button className="h3-btn h3-icon" disabled={!a} title="Next frame (.)" onClick={() => step(1)}><i className="pi pi-step-forward" /></button>
      <input type="range" min={0} max={d || 1} step={0.001} value={t} disabled={!a} onChange={(e) => a && (a.currentTime = Number(e.target.value))} />
      <span className="h3-mono h3-muted">{fmtClock(t)} / {fmtClock(d)}</span>
      <button className={`h3-btn h3-icon${loop ? " h3-on" : ""}`} title="Loop" onClick={() => setLoop(!loop)}><i className="pi pi-replay" /></button>
      <span className="h3-seg" title="Which take you hear">
        <button className={audio === "a" ? "h3-on" : ""} onClick={() => setAudio("a")}>🔊A</button>
        {hasB && <button className={audio === "b" ? "h3-on" : ""} onClick={() => setAudio("b")}>B</button>}
        <button className={audio === "off" ? "h3-on" : ""} onClick={() => setAudio("off")}>off</button>
      </span>
    </div>
  );
}

/** Esc closes, and `onKey` gets keys while focus is inside the window (ComfyUI owns them otherwise).
 * Listened for on the window's own element, so a key it handles (it calls
 * preventDefault) is stopped there, before ComfyUI's window-level shortcuts see it. */
function useWindowKeys(winRef: React.RefObject<HTMLDivElement>, onKey: (e: KeyboardEvent) => void) {
  const cb = useRef(onKey);
  cb.current = onKey;
  useEffect(() => {
    const el = winRef.current;
    const h = (e: KeyboardEvent) => {
      if (isTyping(e.target)) return;
      if (!winRef.current?.contains(document.activeElement)) return;
      if (e.key === "Escape") {
        closeViewer();
        return;
      }
      cb.current(e);
      if (e.defaultPrevented) e.stopPropagation();
    };
    const target: HTMLElement | Window = el ?? window;
    target.addEventListener("keydown", h as EventListener);
    return () => target.removeEventListener("keydown", h as EventListener);
  }, [winRef]);
}

/** The A/B stage: single, side by side, or a wipe with a draggable bar. */
function CompareStage({ mode, paneA, paneB, hasB, emptyB }: {
  mode: CompareMode; paneA: (style?: CSSProperties) => ReactNode; paneB: (style?: CSSProperties) => ReactNode; hasB: boolean; emptyB: string;
}) {
  const [wipe, setWipe] = useState(0.5);
  const stageRef = useRef<HTMLDivElement>(null);
  const wipeStart = (e: RPointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    const stage = stageRef.current;
    if (!stage) return;
    const move = (ev: PointerEvent) => {
      const r = stage.getBoundingClientRect();
      setWipe(Math.min(1, Math.max(0, (ev.clientX - r.left) / r.width)));
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    move(e.nativeEvent);
  };
  return (
    <div ref={stageRef} className={`h3-stage h3-${mode === "side" ? "side" : mode === "wipe" && hasB ? "wipe" : "single"}`}>
      {paneA()}
      {mode === "side" && paneB()}
      {mode === "wipe" && hasB && paneB({ clipPath: `inset(0 0 0 ${wipe * 100}%)` })}
      {mode === "wipe" && hasB && <div className="h3-wipe-bar" style={{ left: `${wipe * 100}%` }} onPointerDown={wipeStart} />}
      {mode === "wipe" && !hasB && <div className="h3-pane-label" style={{ top: "auto", bottom: 4 }}>{emptyB}</div>}
    </div>
  );
}

function ModeButtons({ v }: { v: ViewerState }) {
  return (
    <>
      <span className="h3-seg" title="Compare mode">
        {(["single", "side", "wipe"] as CompareMode[]).map((m) => (
          <button key={m} className={v.mode === m ? "h3-on" : ""} onClick={() => updateViewer({ mode: m, target: m === "single" ? "a" : v.target })}>
            {m === "single" ? "A" : m === "side" ? "A | B" : "wipe"}
          </button>
        ))}
      </span>
      {v.mode !== "single" && (
        <span className="h3-seg" title="Which slot a plain click on a thumbnail loads (ctrl/⌘-click always loads B)">
          <button className={v.target === "a" ? "h3-on" : ""} onClick={() => updateViewer({ target: "a" })}>→A</button>
          <button className={v.target === "b" ? "h3-on" : ""} onClick={() => updateViewer({ target: "b" })}>→B</button>
        </span>
      )}
    </>
  );
}

export function Viewer() {
  const v = useApp((s) => s.viewer);
  const ep = useApp((s) => s.ep);
  if (!v || !ep) return null;
  if (v.kind === "cut") return <CutPlayer ep={ep} v={v} />;
  if (v.kind === "image") return <StillsView ep={ep} v={v} />;
  return <TakesView ep={ep} v={v} />;
}

// ---------------------------------------------------------------------------
// takes: one shot, A/B
// ---------------------------------------------------------------------------

function TakesView({ ep, v }: { ep: string; v: ViewerState }) {
  const curPass = useApp((s) => s.pass);
  const shot = useShotStatus(v.shot, v.pass);
  const st = useStatus(v.pass);
  const cutSt = useShotStatus(v.shot, curPass);
  const [audio, setAudio] = useState<"a" | "b" | "off">("a");
  const [aEl, setAEl] = useState<HTMLVideoElement | null>(null);
  const [bEl, setBEl] = useState<HTMLVideoElement | null>(null);
  const winRef = useRef<HTMLDivElement>(null);
  const { list: targets, seriesDefault } = useTargets();
  const shotCurrent = shotTarget(shot, seriesDefault);

  const mode = v.mode;
  const takeA = shot?.takes.find((t) => t.take === v.a);
  const takeB = mode !== "single" ? shot?.takes.find((t) => t.take === v.b) : undefined;
  useSync(aEl, takeB ? bEl : null);

  useEffect(() => {
    if (aEl) aEl.muted = audio !== "a";
    if (bEl) bEl.muted = audio !== "b";
  }, [aEl, bEl, audio, takeA?.mp4, takeB?.mp4]);

  const fps = takeA?.fps || st?.fps || 24;
  useWindowKeys(winRef, useCallback((e: KeyboardEvent) => {
    if (!aEl) return;
    if (e.key === " ") {
      e.preventDefault();
      if (aEl.paused) void aEl.play();
      else aEl.pause();
    } else if (e.key === "," || e.key === ".") {
      aEl.pause();
      aEl.currentTime = Math.max(0, aEl.currentTime + (e.key === "," ? -1 : 1) / fps);
    }
  }, [aEl, fps]));

  const load = (t: TakeSummary, toB: boolean) => {
    if (!t.mp4) return;
    if (toB) updateViewer({ b: t.take, mode: mode === "single" ? "side" : mode });
    else updateViewer({ a: t.take });
  };

  const url = (t: TakeSummary | undefined) => (t?.mp4 ? api().fileUrl(ep, t.mp4) : undefined);
  const isCutTake = (n: number | undefined) =>
    n != null && !!cutSt && cutSt.cut.pass === v.pass && cutSt.cut.take === n;
  const aspect = aspectOf(st);
  const takes = shot?.takes ?? [];
  // Phase 9b: a locked clip keeps its take
  const locked = useApp(() => lockedPickRefusal(v.shot));

  const pane = (which: "a" | "b", t: TakeSummary | undefined, style?: CSSProperties) => (
    <div className="h3-pane" style={style}>
      <span className="h3-pane-label">
        <span className={`h3-ab h3-${which}`}>{which.toUpperCase()}</span> {t ? tn(t.take) : "—"}
        {t?.target && (
          // each side says which target made it: a shot's takes can mix targets
          <> · <span className={t.target !== shotCurrent ? "h3-b-target" : ""}>{targetLabel(targets, t.target)}</span></>
        )}
        {t && isCutTake(t.take) ? " · cut" : ""}
        {t?.seed ? ` · ${t.seed}` : ""}
      </span>
      {t?.mp4 ? (
        <video
          key={`${which}-${t.mp4}`}
          ref={which === "a" ? setAEl : setBEl}
          src={url(t)}
          preload="auto"
          playsInline
          autoPlay={which === "a"}
          loop
          onContextMenu={(e) => {
            // the take's menu, knowing the frame on screen ("use this frame as the next shot's first frame")
            e.preventDefault();
            const el = e.currentTarget;
            openMenu(e.clientX, e.clientY, v.shot, t.take, v.pass, frameAt(el.currentTime, fps, el.duration, el.ended));
          }}
          onError={(e) => console.warn("h3pipe viewer: video error", (e.currentTarget as HTMLVideoElement).error)}
        />
      ) : (
        <div className="h3-empty-state" style={{ color: "#aaa" }}>
          {t ? `${tn(t.take)} has no video (${t.status})` : which === "b" ? "Ctrl/⌘-click a take below to load B" : "Click a take below"}
        </div>
      )}
    </div>
  );

  const head = (
    <>
      <b>{v.shot}</b>
      <span className="h3-muted h3-small" title="The shot's current target">{v.pass}{shot?.seconds ? ` · ${shot.seconds}s` : ""}{targets ? ` · ${targetLabel(targets, shotCurrent)}` : ""}</span>
      <ModeButtons v={v} />
      {mode !== "single" && takeB && (
        <button className="h3-btn h3-icon" title="Swap A and B" onClick={() => updateViewer({ a: v.b, b: v.a })}>⇄</button>
      )}
      <span className="h3-grow" />
      <button className="h3-btn h3-icon" title="Inspect this shot" onClick={() => openInspector(v.shot, takeA?.take ?? null)}><i className="pi pi-sliders-h" /></button>
      <button className="h3-btn h3-icon" title="Close (Esc)" onClick={closeViewer}><i className="pi pi-times" /></button>
    </>
  );

  return (
    <FloatingWindow storageKey={RECT_KEY} defaultRect={defaultViewerRect} head={head} winRef={winRef}>
      <CompareStage
        mode={mode}
        hasB={!!takeB}
        emptyB="Ctrl/⌘-click a take below to load B"
        paneA={(style) => pane("a", takeA, style)}
        paneB={(style) => pane("b", takeB, style)}
      />
      <Transport a={aEl} fps={fps} audio={audio} setAudio={setAudio} hasB={!!takeB} />
      <div className="h3-vstrip">
        {takes.map((t) => {
          const isA = t.take === v.a;
          const isB = !!takeB && t.take === v.b;
          const stale = realStale(t);
          const tb = takeTargetBadge(t, shotCurrent, targets);
          return (
            <div key={t.take} className={`h3-vtake${isA ? " h3-a" : ""}${isB ? " h3-b" : ""}`}>
              <Thumb
                ep={ep}
                pass={v.pass}
                shot={v.shot}
                take={t}
                height={52}
                aspect={aspect}
                label={`${tn(t.take)}${isCutTake(t.take) ? " ✓" : ""}`}
                onClick={(e) => load(t, e.ctrlKey || e.metaKey || (mode !== "single" && v.target === "b"))}
                onDoubleClick={(e) => {
                  e.stopPropagation();
                  load(t, false);
                }}
              />
              <span className="h3-row" style={{ gap: 3 }}>
                {isA && <span className="h3-ab h3-a">A</span>}
                {isB && <span className="h3-ab h3-b">B</span>}
                <span className={t.status === "ok" ? "h3-muted" : t.status === "failed" ? "h3-err" : ""}>{t.status}</span>
                {stale.length > 0 && <span className="h3-badge h3-b-stale" title={stale.join(", ")}>stale</span>}
                {tb && <span className="h3-badge h3-b-target" title={tb.title}>{tb.label}</span>}
              </span>
            </div>
          );
        })}
        {!takes.length && <div className="h3-muted">No takes.</div>}
      </div>
      <div className="h3-transport" style={{ justifyContent: "flex-end", flexWrap: "wrap" }}>
        <span className="h3-muted h3-small h3-grow">
          Click a take to load {mode === "single" || v.target === "a" ? "A" : "B"} · ctrl/⌘-click loads B · right-click for more
        </span>
        <button className="h3-btn" disabled={!takeA} onClick={() => takeA && openSidecar(v.shot, takeA.take, v.pass)}>Details</button>
        <button
          className="h3-btn"
          disabled={!takeA}
          onClick={() => takeA && openRedo(v.shot, takeA.take, v.pass)}
          title="Open the New take dialog with A's settings"
        >
          <i className="pi pi-refresh" /> New take from {takeA ? tn(takeA.take) : "A"}…
        </button>
        <button
          className="h3-btn h3-primary"
          disabled={!takeA || takeA.status !== "ok" || !takeA.has_video || isCutTake(takeA.take) || !!locked}
          onClick={() => takeA && void pickTake(v.shot, takeA.take, v.pass)}
          title={takeA && isCutTake(takeA.take) ? "The cut already uses A" : locked ?? "Put A in the cut"}
        >
          <i className="pi pi-check" /> {takeA && isCutTake(takeA.take) ? "In the cut" : `Use ${takeA ? tn(takeA.take) : "A"}`}
        </button>
        {takeB && (
          <button
            className="h3-btn"
            disabled={takeB.status !== "ok" || !takeB.has_video || isCutTake(takeB.take) || !!locked}
            title={locked ?? undefined}
            onClick={() => void pickTake(v.shot, takeB.take, v.pass)}
          >
            Use B ({tn(takeB.take)})
          </button>
        )}
        <button
          className="h3-btn h3-icon"
          title="More"
          onClick={(e) => takeA && openMenu(e.clientX, e.clientY, v.shot, takeA.take, v.pass)}
          disabled={!takeA}
        >
          ⋯
        </button>
      </div>
    </FloatingWindow>
  );
}

// ---------------------------------------------------------------------------
// cut: Play all
// ---------------------------------------------------------------------------

interface Engine {
  /** the clip on screen */
  idx: number;
  /** which <video> shows it */
  active: 0 | 1;
  /** clip index loaded in each <video> (-1 none) */
  slot: [number, number];
  /** missing card (and reverse shuttle): how far into the clip, and the last tick's time (performance.now) */
  cardOffset: number;
  lastT: number;
  items: PlayItem[];
}

/**
 * Plays the playlist through two <video> elements: the one on screen, and a
 * spare holding the next clip, loaded and parked at its in point, so a cut is a
 * swap rather than a load. Shots without a usable take are a black card that
 * lasts the shot's duration. The clock is the store's `cutPlay` (the timeline's
 * playhead reads it). Phase 9b: J / L shuttle (2x, 4x; backwards by stepping
 * the picture), and the episode's recorded dialogue under the cut instead of
 * the clips' own sound (one <audio>, kept on the cut's clock).
 */
function CutPlayer({ ep, v }: { ep: string; v: ViewerState }) {
  const items = useApp((s) => currentPlaylist(s));
  const st = useStatus();
  const cp = useApp((s) => s.cutPlay);
  const audioMode = useApp((s) => s.cutAudio);
  const fps = st?.fps || 24;
  // the recording, when there is one that can be played
  const ts = trackState(st?.track);
  const track = ts && !ts.why ? ts : null;
  const recording = audioMode === "recording" && !!track;
  // Phase 9d: the recording wins over every clip's own audio source
  const audioClips = useMemo(() => clipsWithAudio(st?.shots ?? []), [st]);
  const winRef = useRef<HTMLDivElement>(null);
  const v0 = useRef<HTMLVideoElement>(null);
  const v1 = useRef<HTMLVideoElement>(null);
  const au = useRef<HTMLAudioElement>(null);
  const eng = useRef<Engine>({ idx: -1, active: 0, slot: [-1, -1], cardOffset: 0, lastT: 0, items });
  const playing = useRef(cp.playing);
  playing.current = cp.playing;
  const rate = useRef(cp.rate ?? 1);
  rate.current = cp.rate ?? 1;
  const rec = useRef(recording);
  rec.current = recording;
  const [shown, setShown] = useState<{ idx: number; active: 0 | 1 }>({ idx: -1, active: 0 });
  const total = totalDuration(items);
  const sync = useMemo(() => masterSync(items, fps, baseIn(st), anyOutOfOrder(st)), [items, fps, st]);
  const recStart = useRef(sync.start);
  recStart.current = sync.start;

  const els = (): [HTMLVideoElement | null, HTMLVideoElement | null] => [v0.current, v1.current];
  /** forwards at 1x, 2x or 4x (the <video> plays); backwards is stepped here */
  const forward = () => playing.current && rate.current > 0;

  /** Load clip `i` into a <video> and park it at `at` seconds into the file. */
  const loadSlot = useCallback((slot: 0 | 1, i: number, at?: number) => {
    const e = eng.current;
    const el = els()[slot];
    const it = e.items[i];
    e.slot[slot] = i;
    if (!el || !it?.mp4) return;
    const url = api().fileUrl(ep, it.mp4);
    const target = at ?? it.inT;
    if (el.dataset.src !== url) {
      el.dataset.src = url;
      el.src = url;
      el.preload = "auto";
      const seek = () => {
        if (e.slot[slot] === i) el.currentTime = target;
      };
      el.addEventListener("loadedmetadata", seek, { once: true });
      el.load();
    } else if (Math.abs(el.currentTime - target) > 0.01) {
      el.currentTime = target;
    }
  }, [ep]);

  /** Put clip `i` on screen at `offset` seconds into it; preload the next one. */
  const show = useCallback((i: number, offset: number) => {
    const e = eng.current;
    const it = e.items[i];
    const [a, b] = els();
    if (!it) return;
    e.idx = i;
    e.cardOffset = offset;
    e.lastT = performance.now();
    if (it.mp4) {
      const slot: 0 | 1 = e.slot[e.active] === i ? e.active : e.slot[1 - e.active] === i ? ((1 - e.active) as 0 | 1) : e.active;
      e.active = slot;
      loadSlot(slot, i, it.inT + offset);
      const on = slot === 0 ? a : b;
      const off = slot === 0 ? b : a;
      off?.pause();
      if (off) off.muted = true;
      if (on) {
        on.muted = rec.current;
        on.playbackRate = forward() ? rate.current : 1;
        if (forward()) void on.play().catch(() => {});
        else on.pause();
      }
    } else {
      a?.pause();
      b?.pause();
    }
    setShown({ idx: i, active: e.active });
    // the spare: the other <video> while a clip plays, either during a card
    const n = nextVideo(e.items, i);
    if (n >= 0) {
      const spare: 0 | 1 = it.mp4 ? ((1 - e.active) as 0 | 1) : e.active;
      if (e.slot[spare] !== n) loadSlot(spare, n);
    }
  }, [loadSlot]);

  // the clock: follow the clip on screen, cut to the next at its out point
  useEffect(() => {
    let raf = 0;
    const tick = () => {
      const e = eng.current;
      const it = e.items[e.idx];
      const now = performance.now();
      const dt = Math.min(0.25, Math.max(0, (now - e.lastT) / 1000));
      e.lastT = now;
      if (it) {
        if (playing.current && rate.current < 0) {
          // backwards: step the picture (a <video> can't play in reverse)
          let t = cutTime(e.items, e.idx, e.cardOffset) + rate.current * dt;
          if (t <= 0) {
            t = 0;
            store.set((s) => ({ cutPlay: { ...s.cutPlay, playing: false, rate: 1 } }));
          }
          const { index, offset } = locate(e.items, t);
          if (index !== e.idx) show(index, offset);
          else {
            e.cardOffset = offset;
            const el = els()[e.active];
            if (it.mp4 && el && !el.seeking) el.currentTime = fileTime(it, offset);
          }
          reportCutPos(t, e.items[index]?.shot ?? it.shot);
        } else {
          let offset: number;
          let done = false;
          if (it.mp4) {
            const el = els()[e.active];
            const ft = el ? el.currentTime : it.inT;
            offset = clipOffset(it, ft);
            e.cardOffset = offset;
            // a clip that can't load (el.error) is passed over rather than stalling the cut
            done = playing.current && !!el && (atOutPoint(it, ft, fps) || el.ended || !!el.error);
          } else {
            if (playing.current) e.cardOffset += dt * rate.current;
            offset = e.cardOffset;
            done = playing.current && offset >= it.dur;
          }
          if (done) {
            if (e.idx + 1 < e.items.length) {
              show(e.idx + 1, 0);
            } else {
              els().forEach((x) => x?.pause());
              reportCutPos(totalDuration(e.items), it.shot, true);
            }
          } else {
            reportCutPos(cutTime(e.items, e.idx, offset), it.shot);
          }
        }
      }
      // the recording follows the cut's clock (as h3assemble --audio master lays it)
      const a = au.current;
      if (a) {
        const want = recordingAt(recStart.current, store.get().cutPlay.pos);
        const dur = Number.isFinite(a.duration) ? a.duration : Infinity;
        if (rec.current && forward() && want < dur) {
          if (a.playbackRate !== rate.current) a.playbackRate = rate.current;
          if (needsResync(a.currentTime, want, 0.15 * rate.current)) a.currentTime = want;
          if (a.paused) void a.play().catch(() => {});
        } else if (!a.paused) {
          a.pause();
        }
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [fps, show]);

  // seek requests (Play all, Play from here, a click on the timeline or the ruler, the slider)
  const seekN = cp.seek?.n;
  useEffect(() => {
    if (seekN == null) return;
    const t = store.get().cutPlay.seek?.t ?? 0;
    const { index, offset } = locate(eng.current.items, t);
    if (index >= 0) show(index, offset);
    const a = au.current;
    if (a && rec.current) a.currentTime = recordingAt(recStart.current, t);
  }, [seekN, show]);

  // play / pause / shuttle speed
  const cpRate = cp.rate ?? 1;
  useEffect(() => {
    const e = eng.current;
    const it = e.items[e.idx];
    e.lastT = performance.now();
    if (!it) return;
    if (it.mp4) {
      const el = els()[e.active];
      if (!el) return;
      el.playbackRate = cp.playing && cpRate > 0 ? cpRate : 1;
      if (cp.playing && cpRate > 0) void el.play().catch(() => {});
      else el.pause();
    }
  }, [cp.playing, cpRate]);

  // clips' sound or the recording
  useEffect(() => {
    const e = eng.current;
    const el = els()[e.active];
    if (el && e.items[e.idx]?.mp4) el.muted = recording;
    if (!recording) au.current?.pause();
  }, [recording]);

  // the cut changed under us (a pick, a trim, a move, a render finishing): re-place the playhead
  useEffect(() => {
    const e = eng.current;
    const prev = e.items;
    e.items = items;
    if (prev === items) return;
    const was = prev[e.idx];
    const now = items[e.idx];
    const same = was && now && was.shot === now.shot && was.mp4 === now.mp4 && was.inT === now.inT && was.outT === now.outT;
    // slots hold indexes into the old list: forget them unless the clip is the same
    e.slot = [
      e.slot[0] >= 0 && prev[e.slot[0]]?.mp4 === items[e.slot[0]]?.mp4 ? e.slot[0] : -1,
      e.slot[1] >= 0 && prev[e.slot[1]]?.mp4 === items[e.slot[1]]?.mp4 ? e.slot[1] : -1,
    ];
    if (!same) {
      const { index, offset } = locate(items, store.get().cutPlay.pos);
      if (index >= 0) show(index, offset);
    } else {
      const n = nextVideo(items, e.idx);
      const spare: 0 | 1 = now.mp4 ? ((1 - e.active) as 0 | 1) : e.active;
      if (n >= 0 && e.slot[spare] !== n) loadSlot(spare, n);
    }
  }, [items, show, loadSlot]);

  useWindowKeys(winRef, useCallback((e: KeyboardEvent) => {
    if (cutKey(e)) {
      e.preventDefault();
    } else if ((e.key === "ArrowRight" || e.key === "ArrowLeft") && !e.altKey && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      const i = eng.current.idx + (e.key === "ArrowRight" ? 1 : -1);
      const it = eng.current.items[Math.max(0, i)];
      if (it) seekCut(it.start);
    }
  }, []));

  const cur = items[shown.idx];
  const jump = (d: number) => {
    const i = Math.min(items.length - 1, Math.max(0, (eng.current.idx < 0 ? 0 : eng.current.idx) + d));
    if (items[i]) seekCut(items[i].start);
  };
  const missingCount = items.filter((x) => !x.mp4).length;
  const speed = cp.playing && cpRate !== 1 ? `${cpRate < 0 ? "◀ " : ""}${Math.abs(cpRate)}×` : "";
  const driftTitle = sync.warnings.length
    ? `The recording won't line up everywhere:\n• ${sync.warnings.join("\n• ")}${sync.drifts.length ? `\n\nOff by more than a frame: ${sync.drifts.slice(0, 12).map((d) => `${d.shot} ${d.drift > 0 ? "+" : ""}${d.drift.toFixed(2)} s`).join(", ")}${sync.drifts.length > 12 ? "…" : ""}` : ""}`
    : "The recording lines up with every clip's dialogue window";

  const head = (
    <>
      <b>Play all</b>
      <span className="h3-muted h3-small">
        {v.pass} · {items.length} shots · {fmtClock(total)}{missingCount ? ` · ${missingCount} missing` : ""}
      </span>
      <span className="h3-grow" />
      {cur && (
        <button
          className="h3-btn"
          title="Open this shot's takes in the viewer"
          onClick={() => {
            setCutPlaying(false);
            openViewer(cur.shot, cur.take, null, "single", cur.pass);
          }}
        >
          <i className="pi pi-images" /> {cur.shot} takes
        </button>
      )}
      <button className="h3-btn h3-icon" title="Inspect the shot on screen" disabled={!cur} onClick={() => cur && openInspector(cur.shot, cur.take)}><i className="pi pi-sliders-h" /></button>
      <button className="h3-btn h3-icon" title="Close (Esc)" onClick={closeViewer}><i className="pi pi-times" /></button>
    </>
  );

  return (
    <FloatingWindow storageKey={RECT_KEY} defaultRect={defaultViewerRect} head={head} winRef={winRef}>
      <div
        className="h3-stage h3-single h3-cutstage"
        onClick={() => setCutPlaying(!cp.playing)}
        title="Click or space: play / pause · J / K / L: back / stop / forward (again: faster) · ←/→: previous / next shot · I / O: trim the clip to start / end here · Ctrl+Z: undo a cut edit"
      >
        {[v0, v1].map((r, i) => (
          <video
            key={i}
            ref={r}
            playsInline
            preload="auto"
            className={cur?.mp4 && shown.active === i ? "h3-on" : ""}
            onError={(e) => console.warn("h3pipe play all: video error", (e.currentTarget as HTMLVideoElement).error)}
          />
        ))}
        {cur && !cur.mp4 && (
          <div className="h3-missing-card">
            <b>{cur.shot}</b>
            <span>missing · {cur.why}</span>
          </div>
        )}
        {cur && (
          <span className="h3-pane-label">
            {cur.shot} {cur.take != null && cur.mp4 ? tn(cur.take) : ""}{cur.pass !== v.pass ? ` (${cur.pass})` : ""} · {shown.idx + 1}/{items.length}
            {speed ? ` · ${speed}` : ""}
            {recording ? " · recording" : ""}
          </span>
        )}
        {!items.length && <div className="h3-empty-state">The cut is empty.</div>}
      </div>
      {track && (
        <audio
          ref={au}
          preload="auto"
          src={api().fileUrl(ep, track.path)}
          onError={(e) => console.warn("h3pipe play all: recording error", (e.currentTarget as HTMLAudioElement).error)}
        />
      )}
      <div className="h3-transport">
        <button className="h3-btn h3-icon" title="Previous shot (←)" onClick={() => jump(-1)}><i className="pi pi-step-backward" /></button>
        <button className="h3-btn h3-icon" title="Play / pause (space)" onClick={() => setCutPlaying(!cp.playing)}>
          <i className={cp.playing ? "pi pi-pause" : "pi pi-play"} />
        </button>
        <button className="h3-btn h3-icon" title="Next shot (→)" onClick={() => jump(1)}><i className="pi pi-step-forward" /></button>
        <input type="range" min={0} max={total || 1} step={0.01} value={Math.min(cp.pos, total)} onChange={(e) => seekCut(Number(e.target.value))} />
        <span className="h3-mono h3-muted">{fmtClock(cp.pos)} / {fmtClock(total)}</span>
        {ts && (
          <span
            className="h3-seg"
            title={ts.why
              ? `Audio: ${ts.why}, so Play all plays the clips' own sound`
              : `Audio: each clip's own sound (and any clip's own audio source), or the recorded dialogue (${ts.path}) under the cut, as h3assemble --audio master lays it.\n\n${RECORDING_IGNORES_AUDIO}`}
          >
            <button className={!recording ? "h3-on" : ""} onClick={() => setCutAudio("clips")}>clips</button>
            <button className={recording ? "h3-on" : ""} disabled={!!ts.why} onClick={() => setCutAudio("recording")}>recording</button>
          </span>
        )}
        {recording && audioClips.length > 0 && (
          <span className="h3-muted h3-small h3-ell" title={`${RECORDING_IGNORES_AUDIO}\n\n${audioClips.join(", ")}`}>
            {audioClips.length} clip{audioClips.length > 1 ? "s' own" : "'s own"} audio ignored
          </span>
        )}
        {recording && sync.warnings.length > 0 && (
          <span className="h3-badge h3-b-drift" title={driftTitle}>
            <i className="pi pi-exclamation-triangle" /> drifts
          </span>
        )}
      </div>
    </FloatingWindow>
  );
}

// ---------------------------------------------------------------------------
// image: a ref's candidates
// ---------------------------------------------------------------------------

function StillsView({ ep, v }: { ep: string; v: ViewerState }) {
  const ref = useApp((s) => (s.ep && v.ref ? s.refs[s.ep]?.find((r) => r.id === v.ref) : undefined));
  const busy = useApp((s) => !!v.ref && !!s.busy[`refpick|${v.ref}`]);
  const winRef = useRef<HTMLDivElement>(null);
  useWindowKeys(winRef, () => {});
  const view = v.view ?? null;
  const takes = ref ? takesOf(ref, view) : [];
  const picked = ref ? (view ? viewOf(ref, view)?.picked : ref.picked) ?? null : null;
  const mode = v.mode;
  const tA = takes.find((t) => t.take === v.a);
  const tB = mode !== "single" ? takes.find((t) => t.take === v.b) : undefined;
  const src = (t: RefTake | undefined) => (t?.image ? api().refFileUrl(ep, t.image) : undefined);

  const load = (t: RefTake, toB: boolean) => {
    if (!t.image) return;
    if (toB) updateViewer({ b: t.take, mode: mode === "single" ? "side" : mode });
    else updateViewer({ a: t.take });
  };

  const pane = (which: "a" | "b", t: RefTake | undefined, style?: CSSProperties) => (
    <div className="h3-pane" style={style}>
      <span className="h3-pane-label">
        <span className={`h3-ab h3-${which}`}>{which.toUpperCase()}</span> {t ? tn(t.take) : "—"}
        {t && t.take === picked ? " · live" : ""}
        {t?.seed ? ` · ${t.seed}` : ""}
        {t?.source === "imported" ? " · imported" : ""}
      </span>
      {t?.image ? (
        <img className="h3-still" src={src(t)} alt={`${tn(t.take)}`} draggable={false} />
      ) : (
        <div className="h3-empty-state" style={{ color: "#aaa" }}>
          {t ? `${tn(t.take)} has no image (${t.status})` : which === "b" ? "Ctrl/⌘-click a candidate below to load B" : "Click a candidate below"}
        </div>
      )}
    </div>
  );

  // a live file (a character's stitched sheet, a prop, a plate) is not a take,
  // so it gets one plain pane of its own rather than the A/B candidate stage
  if (v.file) {
    return (
      <FloatingWindow
        storageKey={RECT_KEY}
        defaultRect={defaultViewerRect}
        winRef={winRef}
        head={
          <>
            <b>{ref?.name ?? v.ref}</b>
            <span className="h3-muted h3-small">live · {v.file}</span>
            <span className="h3-grow" />
            <button className="h3-btn h3-icon" title="Close (Esc)" onClick={closeViewer}>
              <i className="pi pi-times" />
            </button>
          </>
        }
      >
        {/* the stage is what fits a picture to the window: `.h3-stage img.h3-still`
            is the rule that scales it. A character sheet is a 4096x1024 strip, so
            without it the four panels arrive at full size and run off the edge. */}
        <div className="h3-stage h3-single">
          <div className="h3-pane">
            <img className="h3-still" src={api().refFileUrl(ep, v.file)} alt={v.file}
                 draggable={false} />
          </div>
        </div>
      </FloatingWindow>
    );
  }

  const head = (
    <>
      <b>{ref?.name ?? v.ref}</b>
      <span className="h3-muted h3-small">{view ? viewLabel(view) : ref?.kind}</span>
      <ModeButtons v={v} />
      {mode !== "single" && tB && (
        <button className="h3-btn h3-icon" title="Swap A and B" onClick={() => updateViewer({ a: v.b, b: v.a })}>⇄</button>
      )}
      <span className="h3-grow" />
      <button className="h3-btn h3-icon" title="Close (Esc)" onClick={closeViewer}><i className="pi pi-times" /></button>
    </>
  );

  return (
    <FloatingWindow storageKey={RECT_KEY} defaultRect={defaultViewerRect} head={head} winRef={winRef}>
      {!ref && <div className="h3-empty-state">Loading the ref…</div>}
      {ref && (
        <>
          <CompareStage
            mode={mode}
            hasB={!!tB}
            emptyB="Ctrl/⌘-click a candidate below to load B"
            paneA={(style) => pane("a", tA, style)}
            paneB={(style) => pane("b", tB, style)}
          />
          <div className="h3-vstrip">
            {takes.map((t) => {
              const isA = t.take === v.a;
              const isB = !!tB && t.take === v.b;
              return (
                <div key={t.take} className={`h3-vtake${isA ? " h3-a" : ""}${isB ? " h3-b" : ""}`}>
                  <div
                    className={`h3-thumb h3-refthumb${t.image ? "" : " h3-empty"}`}
                    style={{ height: 60, width: 60, backgroundImage: t.image ? `url("${src(t)}")` : undefined }}
                    title={`${tn(t.take)} · ${t.status}${t.take === picked ? " · live" : ""}`}
                    onClick={(e) => load(t, e.ctrlKey || e.metaKey || (mode !== "single" && v.target === "b"))}
                  >
                    {!t.image && <span>{t.status}</span>}
                    <span className="h3-thumb-label">{tn(t.take)}{t.take === picked ? " ✓" : ""}</span>
                  </div>
                  <span className="h3-row" style={{ gap: 3 }}>
                    {isA && <span className="h3-ab h3-a">A</span>}
                    {isB && <span className="h3-ab h3-b">B</span>}
                    <span className={t.status === "ok" ? "h3-muted" : t.status === "failed" ? "h3-err" : ""}>{t.status}</span>
                  </span>
                </div>
              );
            })}
            {!takes.length && <div className="h3-muted">No candidates.</div>}
          </div>
          <div className="h3-transport" style={{ justifyContent: "flex-end", flexWrap: "wrap" }}>
            <span className="h3-muted h3-small h3-grow">Click a candidate to load {mode === "single" || v.target === "a" ? "A" : "B"} · ctrl/⌘-click loads B</span>
            <button
              className="h3-btn h3-primary"
              disabled={!tA || tA.status !== "ok" || tA.take === picked || busy}
              onClick={() => tA && v.ref && void pickRef(v.ref, view, tA.take)}
            >
              <i className="pi pi-check" /> {tA && tA.take === picked ? "Live" : `Pick ${tA ? tn(tA.take) : "A"}`}
            </button>
            {tB && (
              <button className="h3-btn" disabled={tB.status !== "ok" || tB.take === picked || busy} onClick={() => v.ref && void pickRef(v.ref, view, tB.take)}>
                Pick B ({tn(tB.take)})
              </button>
            )}
          </div>
        </>
      )}
    </FloatingWindow>
  );
}
