import { memo, useMemo, useState, type MouseEvent } from "react";
import {
  build, loadEpisodes, openBrowse, openInspector, openMenu, openSource, openViewer, pickTake, playAll, refreshEpisode,
  renderStale, requestRender, select, selectEpisode, setPass, toggleExpanded, toggleSequence,
} from "../actions";
import { host } from "../host";
import {
  ESTIMATE_TITLE, cutTake, fmtSeconds, fmtWhen, groupBySequence, lengthEstimated, realStale, shotBadges, staleTitle, tn,
} from "../lib/format";
import { missingShots, passProgress, progressLine, progressTitle, staleReasons, staleShots } from "../lib/progress";
import { shotTarget, takeTargetBadge, targetBadges } from "../lib/targets";
import { renderingTakes, statusKey, store, useApp } from "../store";
import type { Pass, ShotStatus, TakeSummary, TargetList } from "../types";
import { aspectOf, useStatus } from "./hooks";
import { MissingRefsSummary } from "./MissingRefs";
import { EpisodeTarget } from "./Readiness";
import { useTargets } from "./Targets";
import { Badges, Progress, Thumb, statusClass } from "./Thumb";
import { TrackButton, TrackLine } from "./Track";

const BROWSE = "__browse__";

export function PassToggle() {
  const pass = useApp((s) => s.pass);
  return (
    <span className="h3-seg" title="Which pass to show and render">
      {(["proxy", "final"] as Pass[]).map((p) => (
        <button key={p} className={pass === p ? "h3-on" : ""} onClick={() => setPass(p)}>
          {p}
        </button>
      ))}
    </span>
  );
}

function EpisodeHeader() {
  const episodes = useApp((s) => s.episodes);
  const episodesError = useApp((s) => s.episodesError);
  const config = useApp((s) => s.config);
  const configError = useApp((s) => s.configError);
  const ep = useApp((s) => s.ep);

  if (configError && !config) {
    return (
      <div className="h3-pad h3-col">
        <div className="h3-note h3-note-err">Can't read the h3pipe config: {configError}</div>
        <button className="h3-btn" onClick={() => void loadEpisodes()}>Retry</button>
      </div>
    );
  }
  if (config && !config.roots.length) {
    return (
      <div className="h3-pad h3-col">
        <div className="h3-title">Welcome to h3pipe</div>
        <div className="h3-muted">
          Tell the editor where your shows live: a folder that holds episode folders (each with a
          <span className="h3-mono"> series.json</span> and a script). Browse to it and add it as a root, or
          double-click an episode to open it straight away.
        </div>
        <div className="h3-row">
          <button className="h3-btn h3-primary" onClick={() => openBrowse({ purpose: "roots" })}>
            <i className="pi pi-folder-open" /> Browse for a folder…
          </button>
        </div>
      </div>
    );
  }
  return (
    <div className="h3-pad h3-col" style={{ gap: 4 }}>
      <div className="h3-row">
        <select
          className="h3-in h3-grow"
          value={ep ?? ""}
          onChange={(e) => (e.target.value === BROWSE ? openBrowse({ purpose: "roots" }) : selectEpisode(e.target.value || null))}
          title={ep ?? ""}
        >
          {!episodes?.length && <option value="">{episodes ? "No episodes found" : "Loading…"}</option>}
          {episodes?.map((e) => (
            <option key={e.ep} value={e.ep} title={e.ep}>
              {e.series ? `${e.series} · ` : ""}{e.name}{e.title && e.title !== e.series ? ` — ${e.title}` : ""}
              {!e.built.proxy && !e.built.final ? " (not built)" : ""}
            </option>
          ))}
          <option value={BROWSE}>Browse…</option>
        </select>
        <button className="h3-btn h3-icon" title="Rescan the project roots" onClick={() => void loadEpisodes()}>
          <i className="pi pi-refresh" />
        </button>
        <button className="h3-btn h3-icon" title="Project folders: browse, add or remove roots, open an episode" onClick={() => openBrowse({ purpose: "roots" })}>
          <i className="pi pi-folder" />
        </button>
      </div>
      {episodesError && <div className="h3-note h3-note-err">{episodesError}</div>}
      {episodes && !episodes.length && (
        <div className="h3-muted h3-small">
          No episode folders under {config?.roots.join(", ")}. An episode needs a series.json (here or in its parent) and a script.
        </div>
      )}
    </div>
  );
}

function BuildBar() {
  const ep = useApp((s) => s.ep);
  const b = useApp((s) => s.build);
  const pass = useApp((s) => s.pass);
  const st = useStatus();
  const renderBusy = useApp((s) => Object.keys(s.busy).some((k) => k.startsWith("render|")));
  const [open, setOpen] = useState(true);
  if (!ep) return null;
  const missing = missingShots(st?.shots);
  // P2: shots whose newest take is stale for a reason a re-render settles
  const stale = staleShots(st?.shots);
  const why = staleReasons(st?.shots);
  const failed = b.result && !b.result.ok;
  return (
    <div className="h3-pad h3-col" style={{ gap: 4, paddingTop: 0 }}>
      <div className="h3-row h3-wrap">
        <PassToggle />
        <button className="h3-btn" disabled={b.busy} onClick={() => void build()} title="Run h3build for both passes from the script and series config">
          <i className={b.busy ? "pi pi-spin pi-spinner" : "pi pi-cog"} /> {b.busy ? "Building…" : "Build"}
        </button>
        <button
          className="h3-btn"
          disabled={!missing.length || renderBusy}
          title={missing.length ? `Queue a ${pass} take for each shot without one: ${missing.join(", ")}` : "Every shot has a take"}
          onClick={() => requestRender(missing, false, "Render missing")}
        >
          <i className="pi pi-play" /> Render missing{missing.length ? ` (${missing.length})` : ""}
        </button>
        <button
          className="h3-btn"
          disabled={!stale.length || renderBusy}
          title={stale.length
            ? `Re-render the shots whose newest take is out of date (`
              + Object.entries(why).map(([r, n]) => `${n} ${r}`).join(", ")
              + `), each at its built seed so only the change shows: ${stale.join(", ")}`
            : "No take is out of date"}
          onClick={() => renderStale()}
        >
          <i className="pi pi-refresh" /> Re-render stale{stale.length ? ` (${stale.length})` : ""}
        </button>
      </div>
      {st && <MissingRefsSummary shots={st.shots} />}
      <TrackLine />
      {(b.result || b.error) && (
        <div className={`h3-note ${failed || b.error ? "h3-note-err" : "h3-note-info"}`}>
          <div className="h3-row">
            <span className="h3-grow">{b.error ? "Build didn't run" : failed ? "Build failed" : "Built"}</span>
            <button className="h3-link" onClick={() => setOpen(!open)}>{open ? "hide" : "show"}</button>
            <button className="h3-link" onClick={() => store.set({ build: { busy: false, result: null, error: null } })}>✕</button>
          </div>
          {open && b.error && <div className="h3-err">{b.error}</div>}
          {open && b.result && (["final", "proxy"] as Pass[]).map((p) => {
            const r = b.result!.passes[p];
            if (!r) return null;
            return (
              <div key={p}>
                <div className="h3-h">{p} {r.ok ? "ok" : "failed"}</div>
                {r.error && <pre className="h3-pre h3-err">{r.error}</pre>}
                {r.report && <pre className="h3-pre" style={{ maxHeight: 140 }}>{r.report}</pre>}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

const TakeRow = memo(function TakeRow({ ep, pass, s, t, aspect, selected, rendering, progress, targets, shotCurrent }: {
  ep: string; pass: Pass; s: ShotStatus; t: TakeSummary; aspect: number; selected: boolean; rendering: boolean;
  progress?: { value: number; max: number };
  targets: TargetList | null; shotCurrent: string;
}) {
  const tb = takeTargetBadge(t, shotCurrent, targets);
  const isCut = !s.cut.placeholder && s.cut.take === t.take;
  const stale = realStale(t);
  const usable = t.status === "ok" && t.has_video;
  const pickBusy = useApp((st) => !!st.busy[`pick|${s.shot}`]);
  const onMenu = (e: MouseEvent) => {
    e.preventDefault();
    openMenu(e.clientX, e.clientY, s.shot, t.take, pass);
  };
  return (
    <div
      className={`h3-take${selected ? " h3-sel" : ""}`}
      onClick={() => select(s.shot, t.take)}
      onDoubleClick={() => t.mp4 && openViewer(s.shot, t.take, null, "single", pass)}
      onContextMenu={onMenu}
    >
      <Thumb ep={ep} pass={pass} shot={s.shot} take={t} height={40} aspect={aspect} />
      <div className="h3-take-info">
        <div className="h3-row">
          <span className={`h3-dot ${statusClass(t.status, rendering)}`} />
          <b>{tn(t.take)}</b>
          <span className="h3-muted">{rendering ? "rendering" : t.status}</span>
          {isCut && <span className="h3-badge h3-b-cut" title={s.cut.picked ? "Picked in cut.json" : "The latest usable take"}>{s.cut.picked ? "cut (picked)" : "cut"}</span>}
          {tb && <span className="h3-badge h3-b-target" title={tb.title}>{tb.label}</span>}
          <span className="h3-grow" />
          {usable && !isCut && (
            <button className="h3-btn" disabled={pickBusy} title="Use this take in the cut" onClick={(e) => { e.stopPropagation(); void pickTake(s.shot, t.take); }}>
              Use
            </button>
          )}
          <button className="h3-btn h3-icon" title="More" onClick={(e) => { e.stopPropagation(); onMenu(e); }}>⋯</button>
        </div>
        {rendering && progress && <Progress value={progress.value} max={progress.max} />}
        <div className="h3-row h3-small">
          <span className="h3-mono h3-ell" title={t.seed ?? "no sidecar"}>{t.seed != null ? `seed ${t.seed}` : "no sidecar"}</span>
          {t.seed_source && <span className="h3-muted">{t.seed_source}</span>}
        </div>
        {(stale.length > 0 || t.overrides.length > 0) && (
          <div className="h3-badges">
            {stale.map((r) => <span key={r} className="h3-badge h3-b-stale" title={staleTitle([r])}>stale: {r}</span>)}
            {t.overrides.length > 0 && <span className="h3-badge h3-b-override" title={`Rendered with an override of: ${t.overrides.join(", ")}`}>ovr: {t.overrides.join(",")}</span>}
          </div>
        )}
        {t.note && <div className="h3-small h3-ell" title={t.note}>“{t.note}”</div>}
        {t.status === "failed" && t.save_notes && <div className="h3-small h3-err">{t.save_notes}</div>}
        {t.finished && <div className="h3-small h3-muted">{fmtWhen(t.finished)}</div>}
      </div>
    </div>
  );
});

function ShotRow({ ep, pass, s, aspect, targets, seriesDefault }: {
  ep: string; pass: Pass; s: ShotStatus; aspect: number; targets: TargetList | null; seriesDefault: string;
}) {
  const selected = useApp((st) => st.shot === s.shot);
  const selTake = useApp((st) => (st.shot === s.shot ? st.take : null));
  const expanded = useApp((st) => !!st.expanded[s.shot]);
  const running = useApp((st) => st.running);
  const prompts = useApp((st) => st.prompts);
  const progress = useApp((st) => st.progress);
  const rendering = useMemo(() => renderingTakes({ running, prompts }, ep, pass, s.shot), [running, prompts, ep, pass, s.shot]);
  const ct = cutTake(s);
  const badges = useMemo(
    () => [...targetBadges(s, targets, seriesDefault, ct), ...shotBadges(s, rendering)],
    [s, rendering, targets, seriesDefault, ct],
  );
  const shotCurrent = shotTarget(s, seriesDefault);
  const runPid = running && prompts[running]?.shot === s.shot && prompts[running]?.pass === pass ? running : null;
  const prog = runPid ? progress[runPid] : undefined;
  const n = s.takes.length;
  return (
    <div className={`h3-shot${selected ? " h3-sel" : ""}`}>
      <div
        className="h3-shot-row"
        onClick={() => select(s.shot, ct?.take ?? null)}
        onDoubleClick={() => (ct ? openViewer(s.shot, ct.take, null, "single", pass) : toggleExpanded(s.shot))}
        onContextMenu={(e) => {
          e.preventDefault();
          openMenu(e.clientX, e.clientY, s.shot, ct?.take ?? null, pass);
        }}
      >
        <span
          className="h3-chev"
          title={expanded ? "Hide takes" : "Show takes"}
          onClick={(e) => {
            e.stopPropagation();
            toggleExpanded(s.shot);
          }}
        >
          {expanded ? "▼" : "▶"}
        </span>
        <Thumb ep={ep} pass={pass} shot={s.shot} take={ct} height={28} aspect={aspect} />
        <div className="h3-col h3-grow" style={{ gap: 1 }}>
          <div className="h3-row">
            <b>{s.shot}</b>
            <span className="h3-muted h3-small" title={lengthEstimated(s) ? ESTIMATE_TITLE : undefined}>
              {lengthEstimated(s) ? <span className="h3-est">≈</span> : null}{fmtSeconds(s.seconds)}{s.size ? ` · ${s.size}` : ""}
            </span>
            <span className="h3-grow" />
            <span className="h3-muted h3-small" title={`${n} take(s)${ct ? `; the cut uses ${tn(ct.take)}` : ""}`}>
              {n ? `${n} take${n > 1 ? "s" : ""}` : ""}{ct ? ` · ${tn(ct.take)}` : ""}
            </span>
          </div>
          {badges.length > 0 && <Badges badges={badges} />}
          {prog && <Progress value={prog.value} max={prog.max} />}
        </div>
      </div>
      {expanded && (
        <div className="h3-takes">
          {!s.takes.length && (
            <div className="h3-row h3-small">
              <span className="h3-muted h3-grow">No takes in {pass}.</span>
              <button className="h3-btn" onClick={() => requestRender([s.shot], false)}>Render</button>
            </div>
          )}
          {[...s.takes].reverse().map((t) => (
            <TakeRow
              key={t.take}
              ep={ep}
              pass={pass}
              s={s}
              t={t}
              aspect={aspect}
              selected={selected && selTake === t.take}
              rendering={rendering.has(t.take)}
              progress={runPid ? progress[runPid] : undefined}
              targets={targets}
              shotCurrent={shotCurrent}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function ShotBin() {
  const ep = useApp((s) => s.ep);
  const pass = useApp((s) => s.pass);
  const st = useStatus();
  const err = useApp((s) => (s.ep ? s.statusError[statusKey(s.ep, s.pass)] : undefined));
  const loading = useApp((s) => (s.ep ? !!s.statusLoading[statusKey(s.ep, s.pass)] : false));
  const collapsed = useApp((s) => s.collapsedSeq);
  const [filter, setFilter] = useState("");
  const groups = useMemo(() => groupBySequence(st?.shots ?? []), [st]);
  const { list: targets, seriesDefault } = useTargets();
  if (!ep) return null;
  if (err && !st) {
    return (
      <div className="h3-pad h3-col">
        <div className="h3-note h3-note-err">{err}</div>
        <div className="h3-row">
          <button className="h3-btn" onClick={() => void refreshEpisode()}>Retry</button>
          <span className="h3-muted h3-small">If the {pass} pass isn't built yet, press Build.</span>
        </div>
      </div>
    );
  }
  // P5: a new episode lands here with nothing built, so say what comes next
  // rather than showing an empty bin
  if (!st) {
    return (
      <div className="h3-empty-state h3-col" style={{ gap: 6 }}>
        {loading ? "Loading…" : (
          <>
            <div>The {pass} pass isn't built yet.</div>
            <div className="h3-muted h3-small">
              <b>Build</b> turns the script into shots. Then the <b>Refs</b> tab, for the pictures
              every shot needs — nothing renders until its references are there.
            </div>
          </>
        )}
      </div>
    );
  }
  const aspect = aspectOf(st);
  const f = filter.trim().toLowerCase();
  // P1: how far through the pass, how fast, how much left (lib/progress.ts)
  const prog = passProgress(st.shots);
  return (
    <>
      <div className="h3-row h3-pad" style={{ paddingTop: 0 }}>
        <input className="h3-in h3-grow" placeholder="Filter shots (id, size, subject)" value={filter} onChange={(e) => setFilter(e.target.value)} />
        <span className="h3-muted h3-small h3-nowrap" title={progressTitle(prog)}>
          {progressLine(prog)}
        </span>
        {loading && <i className="pi pi-spin pi-spinner h3-muted" />}
      </div>
      {err && <div className="h3-pad"><div className="h3-note h3-note-err">{err}</div></div>}
      <div className="h3-scroll h3-sep">
        {groups.map((g) => {
          const key = `${g.sequence}@${g.start}`;
          const shots = f
            ? g.shots.filter((s) => [s.shot, s.size ?? "", ...s.subjects, s.sequence ?? ""].some((x) => x.toLowerCase().includes(f)))
            : g.shots;
          if (!shots.length) return null;
          const isCollapsed = !!collapsed[key] && !f;
          return (
            <div key={key} className="h3-seq">
              <div className="h3-seq-head" onClick={() => toggleSequence(key)}>
                <span className="h3-chev">{isCollapsed ? "▶" : "▼"}</span>
                <b>{g.sequence}</b>
                <span className="h3-muted h3-small">{g.shots.length} shot{g.shots.length > 1 ? "s" : ""} · {fmtSeconds(g.seconds)}</span>
              </div>
              {!isCollapsed && shots.map((s) => <ShotRow key={s.shot} ep={ep} pass={pass} s={s} aspect={aspect} targets={targets} seriesDefault={seriesDefault} />)}
            </div>
          );
        })}
        {!st.shots.length && <div className="h3-empty-state">The {pass} shotlist has no shots.</div>}
      </div>
    </>
  );
}

export function ShotsTab() {
  const ep = useApp((s) => s.ep);
  return (
    <div className="h3-surface">
      <div className="h3-bar" style={{ justifyContent: "space-between" }}>
        <span className="h3-title">Shots</span>
        {ep && (
          <span className="h3-row">
            <button className="h3-btn h3-icon" title="Edit the episode's script" onClick={() => openSource("script")}><i className="pi pi-file-edit" /></button>
            <button className="h3-btn h3-icon" title="Edit the series config (series.json)" onClick={() => openSource("series")}><i className="pi pi-book" /></button>
            <TrackButton />
            <button className="h3-btn h3-icon" title="Inspect the selected shot" onClick={() => openInspector()}><i className="pi pi-sliders-h" /></button>
            <button className="h3-btn h3-icon" title="Play all: the cut from its takes" onClick={() => playAll()}><i className="pi pi-play" /></button>
            <button className="h3-btn h3-icon" title="Open the timeline" onClick={() => host().show("timeline")}><i className="h3-ico-film" /></button>
            <button className="h3-btn h3-icon" title="Open the Refs tab" onClick={() => host().show("refs")}><i className="h3-ico-images" /></button>
            <button className="h3-btn h3-icon" title="Refresh" onClick={() => void refreshEpisode()}><i className="pi pi-refresh" /></button>
          </span>
        )}
      </div>
      <EpisodeHeader />
      <EpisodeTarget />
      <BuildBar />
      <ShotBin />
    </div>
  );
}
