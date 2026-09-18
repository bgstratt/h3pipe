// Mounting the editor's surfaces. Each is its own React root, all sharing the one
// store. Host-agnostic: comfy.ts calls these from ComfyUI's tab `render(el)`
// hooks, and the dev page calls them for its side-by-side layout.

import { StrictMode, type ComponentType } from "react";
import { createRoot, type Root } from "react-dom/client";
import { dismissToast } from "./actions";
import { ContextMenu } from "./components/ContextMenu";
import { RedoDialog, SidecarDialog } from "./components/Dialogs";
import { Inspector } from "./components/Inspector";
import { QueueTab } from "./components/QueueTab";
import { ShotsTab } from "./components/ShotsTab";
import { Timeline } from "./components/Timeline";
import { Viewer } from "./components/Viewer";
import type { Surface } from "./host";
import { useApp } from "./store";
import css from "./styles.css?inline";

const SURFACES: Record<Surface, ComponentType> = {
  shots: ShotsTab,
  inspector: Inspector,
  queue: QueueTab,
  timeline: Timeline,
};

interface Mounted {
  surface: Surface;
  root: Root;
}

const byEl = new WeakMap<Element, Mounted>();
const bySurface = new Map<Surface, Element>();

export function injectStyles(doc: Document = document) {
  if (doc.getElementById("h3pipe-editor-css")) return;
  const style = doc.createElement("style");
  style.id = "h3pipe-editor-css";
  style.textContent = css;
  doc.head.appendChild(style);
}

/**
 * Mount `surface` into `el`. Safe to call repeatedly: ComfyUI calls a custom tab's
 * render(el) from a Vue ref callback, which can fire on every re-render.
 */
export function mountSurface(surface: Surface, el: HTMLElement) {
  injectStyles();
  const cur = byEl.get(el);
  if (cur?.surface === surface) return;
  if (cur) {
    // the host reused this element for another tab
    cur.root.unmount();
    byEl.delete(el);
  }
  // one live copy per surface
  const prevEl = bySurface.get(surface);
  if (prevEl && prevEl !== el) unmountEl(prevEl);
  el.classList.add("h3-root");
  el.style.height = el.style.height || "100%";
  const root = createRoot(el);
  const C = SURFACES[surface];
  root.render(
    <StrictMode>
      <C />
    </StrictMode>,
  );
  byEl.set(el, { surface, root });
  bySurface.set(surface, el);
}

function unmountEl(el: Element) {
  const m = byEl.get(el);
  if (!m) return;
  m.root.unmount();
  byEl.delete(el);
  if (bySurface.get(m.surface) === el) bySurface.delete(m.surface);
}

/**
 * ComfyUI calls a tab's destroy() from onBeforeUnmount, while the element is
 * still in the page, and possibly after render() already mounted a fresh
 * element for the same tab. So: unmount that element once it has actually left
 * the page, and never a newer one.
 */
export function unmountSurface(surface: Surface) {
  const el = bySurface.get(surface);
  if (!el) return;
  setTimeout(() => {
    if (!el.isConnected) unmountEl(el);
  }, 0);
}

function Toasts() {
  const toasts = useApp((s) => s.toasts);
  if (!toasts.length) return null;
  return (
    <div className="h3-toasts">
      {toasts.map((t) => (
        <div key={t.id} className={`h3-toast h3-t-${t.severity}`} onClick={() => dismissToast(t.id)} title="Click to dismiss">
          <div><b>{t.summary}</b></div>
          {t.detail && <div className="h3-small h3-muted" style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{t.detail}</div>}
        </div>
      ))}
    </div>
  );
}

function Overlay({ toasts }: { toasts: boolean }) {
  return (
    <>
      <Viewer />
      <ContextMenu />
      <RedoDialog />
      <SidecarDialog />
      {toasts && <Toasts />}
    </>
  );
}

let overlayRoot: Root | null = null;

/** The floating layer (viewer, context menu, dialogs), on document.body. */
export function mountOverlay(opts: { toasts?: boolean } = {}) {
  if (overlayRoot) return;
  injectStyles();
  const el = document.createElement("div");
  el.id = "h3pipe-editor-overlay";
  el.className = "h3-root h3-overlay";
  document.body.appendChild(el);
  overlayRoot = createRoot(el);
  overlayRoot.render(
    <StrictMode>
      <Overlay toasts={!!opts.toasts} />
    </StrictMode>,
  );
}
