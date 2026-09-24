import { useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  cancelTake, clearRef, closeMenu, copyText, discardTake, generateKeyframe, keyframeFromTake, loadRefs, openClipAudio,
  openIssue,
  openInspector, openRedo, openSidecar, openViewer, pickTake, playAll, requestRender, showInScript, showMissingRefs,
} from "../actions";
import { clearClipAudio, nudgeClip, setTrims, toggleLock } from "../cutActions";
import { audioOf, audioWhy } from "../lib/audioSource";
import { absPath, tn } from "../lib/format";
import { cutNeighbour, keyframeNote, keyframeRefId } from "../lib/keyframes";
import { missingOf } from "../lib/missingRefs";
import { shotTarget } from "../lib/targets";
import { statusKey, useApp } from "../store";
import { useTargets } from "./Targets";

/** The thumbnail context menu, the same in the bin, the timeline and the viewer strip. */
export function ContextMenu() {
  const menu = useApp((s) => s.menu);
  const ep = useApp((s) => s.ep);
  const curPass = useApp((s) => s.pass);
  const st = useApp((s) => (s.ep && s.menu ? s.status[statusKey(s.ep, s.menu.pass)] : undefined));
  const cutSt = useApp((s) => (s.ep ? s.status[statusKey(s.ep, s.pass)] : undefined));
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);
  const { list: targets, seriesDefault } = useTargets();
  const refs = useApp((s) => (s.ep ? s.refs[s.ep] : undefined));

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

  // "Clear keyframe" needs to know whether the shot has one
  useEffect(() => {
    if (menu && ep && !refs) void loadRefs(ep);
  }, [menu, ep, refs]);

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
  // Phase 9b: the clip in the current pass's cut
  const locked = !!cutShot?.cut?.locked;
  const trimmed = !!cutShot && ((cutShot.cut.trim_in ?? 0) > 0 || (cutShot.cut.trim_out ?? 0) > 0);
  const cutIdx = cutSt?.shots.findIndex((x) => x.shot === menu.shot) ?? -1;
  // Phase 9d: what this clip plays, if not its own take's sound
  const clipAudio = audioOf(cutShot?.cut);
  const run = (fn: () => void) => () => {
    closeMenu();
    fn();
  };
  const missing = shot ? missingOf(shot) : [];
  // the timeline plays the current pass's cut
  const inCut = !!cutShot && !cutShot.orphan;
  // continuity keyframes: neighbours in the menu's pass's cut
  const prev = cutNeighbour(st, menu.shot, -1);
  const next = cutNeighbour(st, menu.shot, 1);
  const nextSt = next ? st?.shots.find((x) => x.shot === next) : undefined;
  const kfNote = (s: typeof shot) => {
    const n = keyframeNote(targets, shotTarget(s, seriesDefault));
    return n ? `\nKeyframes are ${n}.` : "";
  };
  const firstKf = refs?.find((r) => r.id === keyframeRefId(menu.shot, "first"));
  const frameLabel = menu.frame == null || menu.frame === "last" ? "its last frame" : `frame ${menu.frame}`;
  const keyframes = (
    <>
      <div className="h3-menu-sep" />
      <button
        disabled={!prev}
        title={(prev
          ? `${menu.shot}'s first keyframe = ${prev}'s last frame, from the take the ${menu.pass} cut uses`
          : `${menu.shot} is the first shot of the ${menu.pass} cut`) + kfNote(shot)}
        onClick={run(() => void keyframeFromTake({ shot: menu.shot, which: "first", pass: menu.pass }))}
      >
        <i className="pi pi-link" /> Use previous shot's last frame as first frame
      </button>
      {take && (
        <button
          disabled={!next || !usable}
          title={(!usable
            ? "Only a finished take with video has frames"
            : next
              ? `${next}'s first keyframe = ${frameLabel} of ${menu.shot} ${tn(take.take)}`
              : `${menu.shot} is the last shot of the ${menu.pass} cut`) + kfNote(nextSt)}
          onClick={run(() => next && void keyframeFromTake({
            shot: next, which: "first", sourceShot: menu.shot, sourceTake: take.take, frame: menu.frame ?? "last", pass: menu.pass,
          }))}
        >
          <i className="pi pi-arrow-right" /> Use this frame as the next shot's first frame
          <span className="h3-muted"> ({frameLabel === "its last frame" ? "last frame" : frameLabel})</span>
        </button>
      )}
      <button
        title={`A still of ${menu.shot}'s opening moment, made by the keyframe image model; it goes live if ${menu.shot} has no first keyframe yet` + kfNote(shot)}
        onClick={run(() => void generateKeyframe(menu.shot, "first"))}
      >
        <i className="pi pi-sparkles" /> Generate first frame
      </button>
      <button
        disabled={!firstKf?.exists}
        title={firstKf?.exists
          ? `Remove ${menu.shot}'s live first keyframe (its candidates stay)${firstKf.need === "required" ? `; ${menu.shot} can't render without one` : `; ${menu.shot} then renders without it`}`
          : `${menu.shot} has no live first keyframe`}
        onClick={run(() => void clearRef(keyframeRefId(menu.shot, "first")))}
      >
        <i className="pi pi-times" /> Clear keyframe
      </button>
    </>
  );
  const common = (
    <>
      {keyframes}
      <div className="h3-menu-sep" />
      <button disabled={!inCut} title="Play the cut from this shot, in the viewer" onClick={run(() => playAll(menu.shot))}>
        <i className="pi pi-forward" /> Play from here
      </button>
      <div className="h3-menu-sep" />
      <button
        disabled={!cutShot}
        title={locked ? `Let ${menu.shot} be moved, trimmed and re-picked again in the ${curPass} cut` : `Keep ${menu.shot} where it is in the ${curPass} cut: no moves, trims or re-picks until unlocked`}
        onClick={run(() => void toggleLock(menu.shot))}
      >
        <i className={locked ? "pi pi-lock-open" : "pi pi-lock"} /> {locked ? "Unlock in the cut" : "Lock in the cut"}
      </button>
      {trimmed && (
        <button disabled={locked} title={locked ? `${menu.shot} is locked` : `Back to the whole take (${cutShot!.cut.trim_in || 0} + ${cutShot!.cut.trim_out || 0} frames trimmed)`} onClick={run(() => void setTrims(menu.shot, 0, 0, `Clear ${menu.shot} trims`))}>
          <i className="pi pi-arrows-h" /> Clear trims
        </button>
      )}
      <button
        title={`Note what is wrong with ${menu.shot} as it rendered (n on the selected clip)`}
        onClick={run(() => openIssue(menu.shot, curPass, menu.take))}
      >
        <i className="pi pi-flag" /> Add issue…
      </button>
      <button
        disabled={!cutShot}
        title={clipAudio
          ? `${menu.shot} plays ${cutShot?.cut.audio_why || audioWhy(clipAudio, menu.shot)} instead of its own sound — change or clear it`
          : `This take's picture with another take's (or a file's) sound under it, cut to ${menu.shot}'s length`}
        onClick={run(() => openClipAudio(menu.shot, curPass))}
      >
        <i className={clipAudio?.source === "none" ? "pi pi-volume-off" : "pi pi-volume-up"} /> Audio from…
        {clipAudio && <span className="h3-muted"> ({cutShot?.cut.audio_why || audioWhy(clipAudio, menu.shot)})</span>}
      </button>
      {clipAudio && (
        <button disabled={locked} title={locked ? `${menu.shot} is locked` : `${menu.shot} back to its own take's sound`} onClick={run(() => void clearClipAudio(menu.shot))}>
          <i className="pi pi-undo" /> Back to its own audio
        </button>
      )}
      <button disabled={locked || cutIdx <= 0} title="Alt+← in the timeline" onClick={run(() => void nudgeClip(menu.shot, -1))}>
        <i className="pi pi-arrow-left" /> Move left in the cut
      </button>
      <button disabled={locked || cutIdx < 0 || cutIdx >= (cutSt?.shots.length ?? 0) - 1} title="Alt+→ in the timeline" onClick={run(() => void nudgeClip(menu.shot, 1))}>
        <i className="pi pi-arrow-right" /> Move right in the cut
      </button>
      {missing.length > 0 && (
        <button title={missing.map((m) => `${m.slot}: ${m.path}`).join("\n")} onClick={run(() => showMissingRefs(menu.shot))}>
          <i className="pi pi-exclamation-triangle" /> Show missing refs ({missing.length})
        </button>
      )}
      <button onClick={run(() => openInspector(menu.shot, take?.take ?? null))}>
        <i className="pi pi-sliders-h" /> Inspect shot
      </button>
      <button disabled={!!shot?.orphan} title={shot?.orphan ? `${menu.shot} is no longer in the script` : `Open the script at ${menu.shot}'s lines`} onClick={run(() => showInScript(menu.shot))}>
        <i className="pi pi-file-edit" /> Show in script
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
            disabled={!usable || isCut || locked}
            title={isCut ? "The cut already uses this take" : locked ? `${menu.shot} is locked in the cut: unlock it to change its take` : usable ? "" : "Only a finished take with video can go in the cut"}
            onClick={run(() => void pickTake(menu.shot, take.take, menu.pass))}
          >
            <i className="pi pi-check" /> Use this take{isCut ? " (in the cut)" : ""}
          </button>
          <button onClick={run(() => openRedo(menu.shot, take.take, menu.pass))}>
            <i className="pi pi-refresh" /> New take like this one…
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
          <button
            className="h3-menu-danger"
            disabled={take.status === "queued"}
            title={take.status === "queued"
              ? "Still queued: cancel the render first"
              : `Move ${tn(take.take)}'s files to the _trash folder beside them (nothing is deleted)${isCut ? "; the cut goes back to the latest usable take" : ""}`}
            onClick={run(() => void discardTake({ ep, pass: menu.pass, shot: menu.shot, take: take.take }))}
          >
            <i className="pi pi-trash" /> Discard take…
          </button>
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
