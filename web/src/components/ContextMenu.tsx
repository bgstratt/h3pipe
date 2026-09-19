import { useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  cancelTake, closeMenu, copyText, openInspector, openRedo, openSidecar, openViewer, pickTake, playAll, requestRender,
  showMissingRefs,
} from "../actions";
import { absPath, tn } from "../lib/format";
import { missingOf } from "../lib/missingRefs";
import { statusKey, useApp } from "../store";

/** The thumbnail context menu, the same in the bin, the timeline and the viewer strip. */
export function ContextMenu() {
  const menu = useApp((s) => s.menu);
  const ep = useApp((s) => s.ep);
  const curPass = useApp((s) => s.pass);
  const st = useApp((s) => (s.ep && s.menu ? s.status[statusKey(s.ep, s.menu.pass)] : undefined));
  const cutSt = useApp((s) => (s.ep ? s.status[statusKey(s.ep, s.pass)] : undefined));
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);

  useLayoutEffect(() => {
    if (!menu || !ref.current) return setPos(null);
    const r = ref.current.getBoundingClientRect();
    setPos({
      left: Math.max(4, Math.min(menu.x, window.innerWidth - r.width - 4)),
      top: Math.max(4, Math.min(menu.y, window.innerHeight - r.height - 4)),
    });
  }, [menu]);

  useEffect(() => {
    if (!menu) return;
    const down = (e: Event) => {
      if (ref.current && !ref.current.contains(e.target as Node)) closeMenu();
    };
    const key = (e: KeyboardEvent) => e.key === "Escape" && closeMenu();
    window.addEventListener("pointerdown", down, true);
    window.addEventListener("keydown", key, true);
    window.addEventListener("blur", closeMenu);
    window.addEventListener("wheel", closeMenu, { passive: true });
    return () => {
      window.removeEventListener("pointerdown", down, true);
      window.removeEventListener("keydown", key, true);
      window.removeEventListener("blur", closeMenu);
      window.removeEventListener("wheel", closeMenu);
    };
  }, [menu]);

  if (!menu || !ep) return null;
  const shot = st?.shots.find((x) => x.shot === menu.shot);
  const take = menu.take != null ? shot?.takes.find((t) => t.take === menu.take) : undefined;
  const cutShot = cutSt?.shots.find((x) => x.shot === menu.shot);
  const isCut = !!take && !!cutShot && cutShot.cut.take === take.take && cutShot.cut.pass === menu.pass;
  const videos = (shot?.takes ?? []).filter((t) => t.mp4);
  const other = menu.take != null
    ? (cutShot && cutShot.cut.pass === menu.pass && cutShot.cut.take !== menu.take && videos.find((t) => t.take === cutShot.cut.take)) ||
      videos.filter((t) => t.take !== menu.take).pop()
    : undefined;
  const usable = !!take && take.status === "ok" && take.has_video;
  const run = (fn: () => void) => () => {
    closeMenu();
    fn();
  };
  const missing = shot ? missingOf(shot) : [];
  // the timeline plays the current pass's cut
  const inCut = !!cutShot && !cutShot.orphan;
  const common = (
    <>
      <div className="h3-menu-sep" />
      <button disabled={!inCut} title="Play the cut from this shot, in the viewer" onClick={run(() => playAll(menu.shot))}>
        <i className="pi pi-forward" /> Play from here
      </button>
      {missing.length > 0 && (
        <button title={missing.map((m) => `${m.slot}: ${m.path}`).join("\n")} onClick={run(() => showMissingRefs(menu.shot))}>
          <i className="pi pi-exclamation-triangle" /> Show missing refs ({missing.length})
        </button>
      )}
      <button onClick={run(() => openInspector(menu.shot, take?.take ?? null))}>
        <i className="pi pi-sliders-h" /> Inspect shot
      </button>
    </>
  );

  return (
    <div
      ref={ref}
      className="h3-menu"
      style={pos ?? { left: menu.x, top: menu.y, visibility: "hidden" }}
      onContextMenu={(e) => e.preventDefault()}
    >
      <div className="h3-menu-head">
        {menu.shot} {take ? tn(take.take) : ""} {menu.pass !== curPass ? `(${menu.pass})` : ""}
        {take ? ` · ${take.status}` : ""}
      </div>
      {take ? (
        <>
          <button disabled={!take.mp4} onClick={run(() => openViewer(menu.shot, take.take, null, "single", menu.pass))}>
            <i className="pi pi-play" /> Play
          </button>
          <button
            disabled={!take.mp4 || !other}
            title={other ? `Opens the viewer with ${tn(take.take)} as A and ${tn(other.take)} as B; click another take to change B` : "Needs another take with video"}
            onClick={run(() => other && openViewer(menu.shot, take.take, other.take, "side", menu.pass))}
          >
            <i className="pi pi-clone" /> Compare with…
          </button>
          <button
            disabled={!usable || isCut}
            title={isCut ? "The cut already uses this take" : usable ? "" : "Only a finished take with video can go in the cut"}
            onClick={run(() => void pickTake(menu.shot, take.take, menu.pass))}
          >
            <i className="pi pi-check" /> Use this take{isCut ? " (in the cut)" : ""}
          </button>
          <button onClick={run(() => openRedo(menu.shot, take.take, menu.pass))}>
            <i className="pi pi-refresh" /> Redo from this take…
          </button>
          <button onClick={run(() => openSidecar(menu.shot, take.take, menu.pass))}>
            <i className="pi pi-info-circle" /> Show details
          </button>
          <button
            disabled={!take.mp4}
            onClick={run(() => take.mp4 && void copyText(absPath(ep, take.mp4), "Path"))}
          >
            <i className="pi pi-copy" /> Copy path
          </button>
          {take.status === "queued" && (
            <button onClick={run(() => void cancelTake({ ep, pass: menu.pass, shot: menu.shot, take: take.take }))}>
              <i className="pi pi-ban" /> Cancel this render
            </button>
          )}
          {common}
        </>
      ) : (
        <>
          <button onClick={run(() => requestRender([menu.shot], false))}>
            <i className="pi pi-play" /> Render this shot{missing.length ? "…" : ""}
          </button>
          <button onClick={run(() => openRedo(menu.shot, null, menu.pass))}>
            <i className="pi pi-refresh" /> Render with settings…
          </button>
          {common}
        </>
      )}
    </div>
  );
}
