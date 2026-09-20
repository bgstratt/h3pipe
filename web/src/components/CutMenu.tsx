// Phase 9b: the timeline's Cut menu (in the overlay, so the bottom panel's
// height never clips it): undo / redo, reset the order or the trims, copy them
// from the other pass.

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { copyCut, redoCut, resetCut, toggleWaves, undoCut } from "../cutActions";
import { clipsWithAudio } from "../lib/audioSource";
import { statusKey, store, useApp } from "../store";

const close = () => store.set({ cutMenu: null });

export function CutMenu() {
  const menu = useApp((s) => s.cutMenu);
  const pass = useApp((s) => s.pass);
  const undo = useApp((s) => (s.ep ? s.cutUndo[statusKey(s.ep, s.pass)] : undefined));
  const waves = useApp((s) => s.waves);
  const st = useApp((s) => (s.ep ? s.status[statusKey(s.ep, s.pass)] : undefined));
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);

  useLayoutEffect(() => {
    if (!menu || !ref.current) return setPos(null);
    const r = ref.current.getBoundingClientRect();
    setPos({
      left: Math.max(4, Math.min(menu.x, window.innerWidth - r.width - 4)),
      // under the button, else above it
      top: menu.y + r.height > window.innerHeight - 4 ? Math.max(4, menu.y - r.height - 30) : menu.y,
    });
  }, [menu]);

  useEffect(() => {
    if (!menu) return;
    const down = (e: Event) => {
      const t = e.target as HTMLElement | null;
      // the Cut button toggles it itself
      if (t && typeof t.closest === "function" && t.closest("[data-cutmenu-btn]")) return;
      if (ref.current && !ref.current.contains(t as Node)) close();
    };
    const key = (e: KeyboardEvent) => e.key === "Escape" && close();
    window.addEventListener("pointerdown", down, true);
    window.addEventListener("keydown", key, true);
    window.addEventListener("blur", close);
    return () => {
      window.removeEventListener("pointerdown", down, true);
      window.removeEventListener("keydown", key, true);
      window.removeEventListener("blur", close);
    };
  }, [menu]);

  if (!menu) return null;
  const other = pass === "proxy" ? "final" : "proxy";
  const run = (fn: () => unknown) => () => {
    close();
    void fn();
  };
  const trimmed = (st?.shots ?? []).filter((s) => (s.cut?.trim_in ?? 0) > 0 || (s.cut?.trim_out ?? 0) > 0).length;
  const moved = (st?.shots ?? []).filter((s) => s.cut?.out_of_order).length;
  const withAudio = clipsWithAudio(st?.shots ?? []).length;
  return (
    <div ref={ref} className="h3-menu" style={pos ?? { left: menu.x, top: menu.y, visibility: "hidden" }} onContextMenu={(e) => e.preventDefault()}>
      <div className="h3-menu-head">The {pass} cut</div>
      <button disabled={!undo?.undo} onClick={run(undoCut)} title="Ctrl+Z (timeline or Play all focused)">
        <i className="pi pi-undo" /> Undo{undo?.undo ? `: ${undo.undo}` : ""}
      </button>
      <button disabled={!undo?.redo} onClick={run(redoCut)} title="Ctrl+Shift+Z">
        <i className="pi pi-refresh" /> Redo{undo?.redo ? `: ${undo.redo}` : ""}
      </button>
      <div className="h3-menu-sep" />
      <button onClick={run(() => resetCut("order"))} title="Back to script order (picks, locks and notes kept)">
        <i className="pi pi-sort-numeric-down" /> Reset order{moved ? ` (${moved} moved)` : ""}
      </button>
      <button onClick={run(() => resetCut("trims"))} title="Every trim back to zero">
        <i className="pi pi-arrows-h" /> Clear trims{trimmed ? ` (${trimmed} trimmed)` : ""}
      </button>
      <button
        disabled={!withAudio}
        onClick={run(() => resetCut("audio"))}
        title={withAudio
          ? `${withAudio} clip${withAudio > 1 ? "s play" : " plays"} someone else's sound: put every one back to its own take's`
          : "Every clip already plays its own take's sound"}
      >
        <i className="pi pi-volume-up" /> Clear audio sources{withAudio ? ` (${withAudio})` : ""}
      </button>
      <button onClick={run(() => resetCut("all"))}>
        <i className="pi pi-replay" /> Reset order, trims and audio
      </button>
      <div className="h3-menu-sep" />
      <button onClick={run(() => copyCut("order"))} title={`The ${other} cut's order onto this one (picks are never copied)`}>
        <i className="pi pi-copy" /> Copy order from {other}
      </button>
      <button onClick={run(() => copyCut("trims"))} title={`The ${other} cut's trims onto this one, converted when the frame rates differ`}>
        <i className="pi pi-copy" /> Copy trims from {other}
      </button>
      <button onClick={run(() => copyCut("all"))} title={`Order, trims and audio sources from the ${other} cut (picks are never copied)`}>
        <i className="pi pi-copy" /> Copy order, trims and audio from {other}
      </button>
      <div className="h3-menu-sep" />
      <button onClick={run(() => toggleWaves())}>
        <i className="pi pi-wave-pulse" /> {waves ? "Hide waveforms" : "Show waveforms"}
      </button>
    </div>
  );
}
