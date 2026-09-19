// A floating window: drag by its head, resize from the corner, remembers its
// place (localStorage). The take viewer and the inspector are both one.

import { useEffect, useRef, useState, type PointerEvent as RPointerEvent, type ReactNode, type RefObject } from "react";

export interface Rect { x: number; y: number; w: number; h: number }

function viewport() {
  return { W: window.innerWidth || 1280, H: window.innerHeight || 800 };
}

export function clampRect(r: Rect): Rect {
  const { W, H } = viewport();
  const w = Math.min(r.w, W);
  const h = Math.min(r.h, H);
  return { w, h, x: Math.min(Math.max(0, r.x), W - 80), y: Math.min(Math.max(0, r.y), H - 40) };
}

function loadRect(key: string, fallback: () => Rect): Rect {
  try {
    const r = JSON.parse(localStorage.getItem(key) ?? "null") as Rect | null;
    if (r && r.w > 200 && r.h > 200) return clampRect(r);
  } catch {
    /* ignore */
  }
  return clampRect(fallback());
}

// Default places, side by side so the viewer and the inspector don't cover each
// other: the inspector on the right edge, the viewer to its left.
export const INSPECTOR_W = 440;
const GAP = 16;

export function defaultInspectorRect(): Rect {
  const { W, H } = viewport();
  const w = Math.min(INSPECTOR_W, Math.max(320, W - 2 * GAP));
  return { x: Math.max(0, W - w - GAP), y: 56, w, h: Math.max(300, Math.min(760, H - 56 - 60)) };
}

export function defaultViewerRect(): Rect {
  const { W, H } = viewport();
  const right = W - INSPECTOR_W - 2 * GAP; // the inspector's left edge, minus a gap
  const w = Math.max(360, Math.min(760, right - GAP));
  const h = Math.max(300, Math.min(560, H - 56 - 60));
  return { x: Math.max(0, right - w), y: 56, w, h };
}

// The window last touched sits on top. Kept in a narrow band below the context
// menu (2100) and the dialogs (2200).
let frontKey = "";
const fronts = new Set<() => void>();

function bringToFront(key: string) {
  if (frontKey === key) return;
  frontKey = key;
  fronts.forEach((f) => f());
}

interface Props {
  storageKey: string;
  defaultRect: () => Rect;
  head: ReactNode;
  children: ReactNode;
  winRef?: RefObject<HTMLDivElement>;
  className?: string;
  minW?: number;
  minH?: number;
}

export function FloatingWindow({ storageKey, defaultRect, head, children, winRef, className, minW, minH }: Props) {
  const own = useRef<HTMLDivElement>(null);
  const ref = winRef ?? own;
  const [rect, setRect] = useState<Rect>(() => loadRect(storageKey, defaultRect));
  const [onTop, setOnTop] = useState(true);

  // a window that opens comes to the front
  useEffect(() => {
    const f = () => setOnTop(frontKey === storageKey);
    fronts.add(f);
    bringToFront(storageKey);
    f();
    return () => {
      fronts.delete(f);
    };
  }, [storageKey]);

  useEffect(() => {
    try {
      localStorage.setItem(storageKey, JSON.stringify(rect));
    } catch {
      /* ignore */
    }
  }, [rect, storageKey]);

  // pick up CSS resize: the browser changes the element's size, not our state
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => {
      const w = el.offsetWidth;
      const h = el.offsetHeight;
      setRect((r) => (r.w === w && r.h === h ? r : { ...r, w, h }));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref]);

  const front = () => bringToFront(storageKey);

  const dragStart = (e: RPointerEvent<HTMLDivElement>) => {
    if ((e.target as HTMLElement).closest("button, select, input, textarea, a, label")) return;
    const el = ref.current;
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

  return (
    <div
      ref={ref}
      className={`h3-window h3-root${className ? " " + className : ""}`}
      style={{ left: rect.x, top: rect.y, width: rect.w, height: rect.h, zIndex: onTop ? 2052 : 2051, minWidth: minW, minHeight: minH }}
      tabIndex={-1}
      onPointerDownCapture={front}
    >
      <div className="h3-window-head" onPointerDown={dragStart}>
        {head}
      </div>
      {children}
    </div>
  );
}
