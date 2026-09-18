import { memo, useEffect, useMemo, useRef } from "react";
import { assemble, openMenu, openViewer, refreshEpisode, select, setZoom } from "../actions";
import { host } from "../host";
import { cutTake, fmtSeconds, groupBySequence, shotBadges, tn } from "../lib/format";
import { ZOOM_MAX, ZOOM_MIN, renderingTakes, statusKey, useApp } from "../store";
import type { EpisodeStatus, Pass, ShotStatus, TakeSummary } from "../types";
import { aspectOf, useSize, useStatus } from "./hooks";
import { PassToggle } from "./ShotsTab";
import { Badges, Progress, mediaStyle, useScrub } from "./Thumb";

const MIN_CLIP = 26;

/** The take a clip shows: the cut's take, from the other pass for a placeholder. */
function clipTake(s: ShotStatus, other: EpisodeStatus | undefined): TakeSummary | undefined {
  if (!s.cut.placeholder) return cutTake(s);
  if (s.cut.take == null) return undefined;
  return other?.shots.find((x) => x.shot === s.shot)?.takes.find((t) => t.take === s.cut.take);
}

const Clip = memo(function Clip({ ep, pass, s, other, zoom, height, aspect, selected, rendering, progress }: {
  ep: string; pass: Pass; s: ShotStatus; other: EpisodeStatus | undefined; zoom: number; height: number;
  aspect: number; selected: boolean; rendering: Set<number>; progress?: { value: number; max: number };
}) {
  const [cell, scrub] = useScrub();
  const take = clipTake(s, other);
  const takePass: Pass = s.cut.placeholder ? s.cut.pass : pass;
  const width = Math.max(MIN_CLIP, Math.round((s.seconds ?? 1) * zoom));
  const mediaW = Math.min(width, Math.round(height * aspect));
  const badges = useMemo(() => shotBadges(s, rendering), [s, rendering]);
  const cls = ["h3-clip", selected && "h3-sel", s.cut.placeholder && "h3-placeholder", s.orphan && "h3-orphan"].filter(Boolean).join(" ");
  const title = [
    `${s.shot} · ${fmtSeconds(s.seconds)}${s.size ? ` · ${s.size}` : ""}`,
    take ? `${s.cut.placeholder ? `${s.cut.pass} ` : ""}${tn(take.take)} (${take.status})` : "no take in the cut",
    ...badges.map((b) => b.title),
    "click: select · double-click: viewer · right-click: menu",
  ].join("\n");
  return (
    <div
      className={cls}
      style={{ width }}
      title={title}
      onClick={() => select(s.shot, take && !s.cut.placeholder ? take.take : null)}
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
      {height > 36 && badges.length > 0 && (
        <div className="h3-clip-top">
          <Badges badges={badges} max={width < 90 ? 1 : 3} />
        </div>
      )}
      <div className="h3-clip-bottom">
        <b>{s.shot}</b>
        {take && width > 60 && <span>{tn(take.take)}</span>}
      </div>
      {progress && <Progress value={progress.value} max={progress.max} />}
    </div>
  );
});

export function Timeline() {
  const ep = useApp((s) => s.ep);
  const pass = useApp((s) => s.pass);
  const zoom = useApp((s) => s.zoom);
  const shot = useApp((s) => s.shot);
  const running = useApp((s) => s.running);
  const prompts = useApp((s) => s.prompts);
  const progress = useApp((s) => s.progress);
  const asm = useApp((s) => s.assemble);
  const err = useApp((s) => (s.ep ? s.statusError[statusKey(s.ep, s.pass)] : undefined));
  const st = useStatus();
  const other = useStatus(pass === "proxy" ? "final" : "proxy");
  const [trackRef, size] = useSize<HTMLDivElement>();
  const wrapRef = useRef<HTMLDivElement>(null);
  const groups = useMemo(() => groupBySequence(st?.shots ?? []), [st]);
  // clip height: the track minus padding and the sequence label
  const clipH = Math.max(24, (size.height || 150) - 6 - 16 - 2);
  const aspect = aspectOf(st);
  const total = st?.shots.reduce((n, s) => n + (s.seconds ?? 0), 0) ?? 0;

  // keep the selected clip in view
  useEffect(() => {
    if (!shot || !wrapRef.current) return;
    const el = wrapRef.current.querySelector<HTMLElement>(`[data-shot="${CSS.escape(shot)}"]`);
    el?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [shot]);

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

  return (
    <div className="h3-surface">
      <div className="h3-bar">
        <span className="h3-title h3-ell" title={ep ?? ""}>{st ? `${st.episode}` : "Timeline"}</span>
        {st && <span className="h3-muted h3-small">{st.shots.length} shots · {fmtSeconds(total)}</span>}
        <PassToggle />
        <span className="h3-row" title="Zoom (ctrl + wheel over the track)">
          <i className="pi pi-search-minus h3-muted" />
          <input type="range" min={ZOOM_MIN} max={ZOOM_MAX} value={zoom} onChange={(e) => setZoom(Number(e.target.value))} style={{ width: 90 }} />
          <i className="pi pi-search-plus h3-muted" />
        </span>
        <span className="h3-grow" />
        <button className="h3-btn" disabled={!ep || asm.busy} title={`Assemble the ${pass} review cut (missing shots are skipped)`} onClick={() => void assemble(true)}>
          <i className={asm.busy ? "pi pi-spin pi-spinner" : "pi pi-video"} /> {asm.busy ? "Assembling…" : "Assemble"}
        </button>
        <button className="h3-btn h3-icon" title="Open the Shots tab" onClick={() => host().show("shots")}><i className="pi pi-list" /></button>
        <button className="h3-btn h3-icon" title="Refresh" disabled={!ep} onClick={() => void refreshEpisode()}><i className="pi pi-refresh" /></button>
      </div>
      <div className="h3-track-wrap" ref={wrapRef}>
        <div ref={trackRef} style={{ position: "absolute", inset: 0, pointerEvents: "none" }} />
        {!ep && <div className="h3-empty-state">Pick an episode in the h3 Shots tab.</div>}
        {ep && err && !st && <div className="h3-pad"><div className="h3-note h3-note-err">{err}</div></div>}
        {ep && st && (
          <div className="h3-track">
            {groups.map((g) => (
              <div key={`${g.sequence}@${g.start}`} className="h3-tl-seq">
                <div className="h3-tl-seq-label" title={`${g.sequence} · ${fmtSeconds(g.seconds)}`}>{g.sequence}</div>
                <div className="h3-tl-clips">
                  {g.shots.map((s) => {
                    const r = renderingTakes({ running, prompts }, ep, pass, s.shot);
                    return (
                      <div key={s.shot} data-shot={s.shot} style={{ height: "100%" }}>
                        <Clip
                          ep={ep}
                          pass={pass}
                          s={s}
                          other={s.cut.placeholder ? other : undefined}
                          zoom={zoom}
                          height={clipH}
                          aspect={aspect}
                          selected={shot === s.shot}
                          rendering={r.size ? r : NO_TAKES}
                          progress={r.size && running ? progress[running] : undefined}
                        />
                      </div>
                    );
                  })}
                </div>
              </div>
            ))}
            {!st.shots.length && <div className="h3-empty-state">No shots.</div>}
          </div>
        )}
      </div>
    </div>
  );
}

const NO_TAKES: Set<number> = new Set();
