import { useCallback, useEffect, useRef, useState, type PointerEvent as RPointerEvent } from "react";
import { closeViewer, openMenu, openRedo, openSidecar, pickTake, updateViewer } from "../actions";
import { api } from "../host";
import { fmtClock, realStale, tn } from "../lib/format";
import { useApp, type CompareMode } from "../store";
import type { TakeSummary } from "../types";
import { aspectOf, useShotStatus, useStatus } from "./hooks";
import { Thumb } from "./Thumb";

interface Rect { x: number; y: number; w: number; h: number }

const RECT_KEY = "h3pipe.viewer.rect";

function initialRect(): Rect {
  try {
    const r = JSON.parse(localStorage.getItem(RECT_KEY) ?? "null") as Rect | null;
    if (r && r.w > 200 && r.h > 200) return clampRect(r);
  } catch {
    /* ignore */
  }
  const w = Math.min(760, window.innerWidth - 40);
  const h = Math.min(560, window.innerHeight - 40);
  return { x: Math.round((window.innerWidth - w) / 2), y: Math.round((window.innerHeight - h) / 3), w, h };
}

function clampRect(r: Rect): Rect {
  const w = Math.min(r.w, window.innerWidth);
  const h = Math.min(r.h, window.innerHeight);
  return { w, h, x: Math.min(Math.max(0, r.x), window.innerWidth - 80), y: Math.min(Math.max(0, r.y), window.innerHeight - 40) };
}

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

export function Viewer() {
  const v = useApp((s) => s.viewer);
  const ep = useApp((s) => s.ep);
  const curPass = useApp((s) => s.pass);
  const shot = useShotStatus(v?.shot, v?.pass);
  const st = useStatus(v?.pass);
  const cutSt = useShotStatus(v?.shot, curPass);
  const [rect, setRect] = useState<Rect>(initialRect);
  const [wipe, setWipe] = useState(0.5);
  const [audio, setAudio] = useState<"a" | "b" | "off">("a");
  const [aEl, setAEl] = useState<HTMLVideoElement | null>(null);
  const [bEl, setBEl] = useState<HTMLVideoElement | null>(null);
  const winRef = useRef<HTMLDivElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);

  const mode: CompareMode = v?.mode ?? "single";
  const takeA = shot?.takes.find((t) => t.take === v?.a);
  const takeB = mode !== "single" ? shot?.takes.find((t) => t.take === v?.b) : undefined;
  useSync(aEl, takeB ? bEl : null);

  useEffect(() => {
    if (aEl) aEl.muted = audio !== "a";
    if (bEl) bEl.muted = audio !== "b";
  }, [aEl, bEl, audio, takeA?.mp4, takeB?.mp4]);

  // remember size/position
  useEffect(() => {
    try {
      localStorage.setItem(RECT_KEY, JSON.stringify(rect));
    } catch {
      /* ignore */
    }
  }, [rect]);

  // pick up CSS resize: the browser changes the element's size, not our state
  useEffect(() => {
    const el = winRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => {
      const w = el.offsetWidth;
      const h = el.offsetHeight;
      setRect((r) => (r.w === w && r.h === h ? r : { ...r, w, h }));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [v != null]);

  const onKey = useCallback((e: KeyboardEvent) => {
    if (!v || !aEl) return;
    const tag = (e.target as HTMLElement | null)?.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
    // only while the viewer has focus: ComfyUI uses space (canvas drag) and others
    if (!winRef.current?.contains(document.activeElement)) return;
    if (e.key === " ") {
      e.preventDefault();
      if (aEl.paused) void aEl.play();
      else aEl.pause();
    } else if (e.key === "," || e.key === ".") {
      aEl.pause();
      aEl.currentTime = Math.max(0, aEl.currentTime + (e.key === "," ? -1 : 1) / (st?.fps || 24));
    } else if (e.key === "Escape") {
      closeViewer();
    }
  }, [v, aEl, st?.fps]);
  useEffect(() => {
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onKey]);

  if (!v || !ep) return null;

  const dragStart = (e: RPointerEvent<HTMLDivElement>) => {
    if ((e.target as HTMLElement).closest("button, select, input")) return;
    const el = winRef.current;
    if (!el) return;
    e.preventDefault();
    const sx = e.clientX;
    const sy = e.clientY;
    const start = { ...rect, w: el.offsetWidth, h: el.offsetHeight };
    const move = (ev: PointerEvent) => setRect(clampRect({ ...start, x: start.x + ev.clientX - sx, y: start.y + ev.clientY - sy }));
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

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

  const pane = (which: "a" | "b", t: TakeSummary | undefined, style?: React.CSSProperties) => (
    <div className="h3-pane" style={style}>
      <span className="h3-pane-label">
        <span className={`h3-ab h3-${which}`}>{which.toUpperCase()}</span> {t ? tn(t.take) : "—"}
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
          onError={(e) => {
            const err = (e.currentTarget as HTMLVideoElement).error;
            console.warn("h3pipe viewer: video error", err);
          }}
        />
      ) : (
        <div className="h3-empty-state" style={{ color: "#aaa" }}>
          {t ? `${tn(t.take)} has no video (${t.status})` : which === "b" ? "Ctrl/⌘-click a take below to load B" : "Click a take below"}
        </div>
      )}
    </div>
  );

  return (
    <div
      ref={winRef}
      className="h3-window h3-root"
      style={{ left: rect.x, top: rect.y, width: rect.w, height: rect.h }}
      tabIndex={-1}
    >
      <div className="h3-window-head" onPointerDown={dragStart}>
        <b>{v.shot}</b>
        <span className="h3-muted h3-small">{v.pass}{shot?.seconds ? ` · ${shot.seconds}s` : ""}</span>
        <span className="h3-seg" title="Compare mode">
          {(["single", "side", "wipe"] as CompareMode[]).map((m) => (
            <button key={m} className={mode === m ? "h3-on" : ""} onClick={() => updateViewer({ mode: m, target: m === "single" ? "a" : v.target })}>
              {m === "single" ? "A" : m === "side" ? "A | B" : "wipe"}
            </button>
          ))}
        </span>
        {mode !== "single" && (
          <span className="h3-seg" title="Which slot a plain click on a take loads (ctrl/⌘-click always loads B)">
            <button className={v.target === "a" ? "h3-on" : ""} onClick={() => updateViewer({ target: "a" })}>→A</button>
            <button className={v.target === "b" ? "h3-on" : ""} onClick={() => updateViewer({ target: "b" })}>→B</button>
          </span>
        )}
        {mode !== "single" && takeB && (
          <button className="h3-btn h3-icon" title="Swap A and B" onClick={() => updateViewer({ a: v.b, b: v.a })}>⇄</button>
        )}
        <span className="h3-grow" />
        <button className="h3-btn h3-icon" title="Close (Esc)" onClick={closeViewer}><i className="pi pi-times" /></button>
      </div>
      <div
        ref={stageRef}
        className={`h3-stage h3-${mode === "side" ? "side" : mode === "wipe" && takeB ? "wipe" : "single"}`}
      >
        {pane("a", takeA)}
        {mode === "side" && pane("b", takeB)}
        {mode === "wipe" && takeB && pane("b", takeB, { clipPath: `inset(0 0 0 ${wipe * 100}%)` })}
        {mode === "wipe" && takeB && <div className="h3-wipe-bar" style={{ left: `${wipe * 100}%` }} onPointerDown={wipeStart} />}
        {mode === "wipe" && !takeB && (
          <div className="h3-pane-label" style={{ top: "auto", bottom: 4 }}>Ctrl/⌘-click a take below to load B</div>
        )}
      </div>
      <Transport a={aEl} fps={st?.fps ?? 24} audio={audio} setAudio={setAudio} hasB={!!takeB} />
      <div className="h3-vstrip">
        {takes.map((t) => {
          const isA = t.take === v.a;
          const isB = !!takeB && t.take === v.b;
          const stale = realStale(t);
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
          title="Open the redo dialog with A's settings"
        >
          <i className="pi pi-refresh" /> Redo from {takeA ? tn(takeA.take) : "A"}
        </button>
        <button
          className="h3-btn h3-primary"
          disabled={!takeA || takeA.status !== "ok" || !takeA.has_video || isCutTake(takeA.take)}
          onClick={() => takeA && void pickTake(v.shot, takeA.take, v.pass)}
          title={takeA && isCutTake(takeA.take) ? "The cut already uses A" : "Put A in the cut"}
        >
          <i className="pi pi-check" /> {takeA && isCutTake(takeA.take) ? "In the cut" : `Use ${takeA ? tn(takeA.take) : "A"}`}
        </button>
        {takeB && (
          <button
            className="h3-btn"
            disabled={takeB.status !== "ok" || !takeB.has_video || isCutTake(takeB.take)}
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
    </div>
  );
}
