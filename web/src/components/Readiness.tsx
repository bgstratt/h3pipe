// Readiness UI (docs/API.md "Readiness, requirement tiers, and the episode
// target"): the status chip, the inline warning under a target picker, the
// episode's Model control in the Shots tab, the What's missing window, and
// the notes a take's `resolved` gives.

import { useEffect, useRef, useState } from "react";
import { closeMissing, copyText, installWorkflow, openMissing, refreshReadiness, revertWorkflow, setEpisodeTarget } from "../actions";
import {
  downloadOf, episodeTargetSourceLabel, folderPath, missingCountText, missingGroups, normReadiness, notReadyBanner,
  pickWarning, readinessBadge, readinessOf, resolutionNotes, searchText, seriesSnippet, seriesTarget, targetCounts,
  targetCountsText, tierText,
} from "../lib/readiness";
import { findTarget, listDefaultTarget, seriesDefaultTarget, targetLabel } from "../lib/targets";
import { useApp } from "../store";
import type { MissingFile, Readiness, Resolution, Target, TargetList } from "../types";
import { CustomTargets } from "./CustomTarget";
import { FloatingWindow, type Rect } from "./FloatingWindow";
import { useStatus } from "./hooks";
import { useTargets } from "./Targets";

// ---------------------------------------------------------------------------
// small pieces
// ---------------------------------------------------------------------------

/** ✓ ready / ◐ degraded / ✗ not ready / ? unknown; nothing when the server sent no readiness. */
export function ReadinessChip({ r, short }: { r: Readiness | null | undefined; short?: boolean }) {
  const b = readinessBadge(r);
  if (!b) return null;
  return (
    <span className={`h3-rd h3-rd-${b.cls}`} title={b.title}>
      {b.icon}{short ? "" : ` ${b.label}`}
    </span>
  );
}

/** A link-styled button that opens What's missing for a target. */
export function MissingLink({ target, children }: { target: string | null; children?: React.ReactNode }) {
  return (
    <button className="h3-link" onClick={(e) => { e.stopPropagation(); openMissing(target); }}>
      {children ?? "What's missing"}
    </button>
  );
}

/**
 * The warning under a target picker when the chosen target is not ready
 * (a required file is missing: its shots are skipped) or degraded.
 */
export function TargetReadinessNote({ id, list }: { id: string | null | undefined; list: TargetList | null }) {
  if (!id) return null;
  const w = pickWarning(targetLabel(list, id), readinessOf(list, id));
  if (!w) return null;
  return (
    <div className={`h3-note h3-small${w.severity === "err" ? " h3-note-err" : ""}`}>
      {w.text}{w.severity === "err" ? " Its shots are skipped until then." : ""} <MissingLink target={id} />
    </div>
  );
}

/** "rendered with base settings: … missing", from a take's sidecar `resolved`. */
export function ResolvedNotes({ resolved, target, list }: {
  resolved: Record<string, Resolution> | null | undefined;
  target?: string | null;
  list: TargetList | null;
}) {
  const notes = resolutionNotes(resolved, findTarget(list, target));
  if (!notes.length) return null;
  return (
    <div className="h3-note h3-note-info h3-small">
      <div className="h3-row">
        <b className="h3-grow">Model substitutions</b>
        {target && <MissingLink target={target} />}
      </div>
      <ul className="h3-missing-list">
        {notes.map((n) => <li key={n}>{n}</li>)}
      </ul>
    </div>
  );
}

function CopyButton({ text, what }: { text: string; what: string }) {
  return (
    <button className="h3-btn h3-icon h3-copy" title={`Copy ${what.toLowerCase()}: ${text}`} onClick={(e) => { e.stopPropagation(); void copyText(text, what); }}>
      <i className="pi pi-copy" />
    </button>
  );
}

// ---------------------------------------------------------------------------
// the episode target (Shots tab)
// ---------------------------------------------------------------------------

/**
 * "Model: <label> [status] · from series.json": the episode's default target.
 * Clicking opens a picker of the video targets with their readiness; the
 * choice is written to overrides.json (PUT /h3pipe/episode-target), and the
 * snippet makes it permanent in series.json.
 */
export function EpisodeTarget() {
  const ep = useApp((s) => s.ep);
  const st = useStatus();
  const busy = useApp((s) => !!s.busy["episode-target"]);
  const { list, video } = useTargets();
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const down = (e: PointerEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    const key = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("pointerdown", down, true);
    window.addEventListener("keydown", key);
    return () => {
      window.removeEventListener("pointerdown", down, true);
      window.removeEventListener("keydown", key);
    };
  }, [open]);

  if (!ep || !st || !list || !video.length) return null;
  const current = seriesDefaultTarget(list, st);
  const r = readinessOf(list, current);
  const source = st.target_source ?? null;
  const srcLabel = episodeTargetSourceLabel(source);
  const series = seriesTarget(st, list);
  const counts = targetCounts(st.shots);
  const known = counts.episode + counts.own > 0;
  const banner = notReadyBanner(targetLabel(list, current), r);
  const choose = (id: string | null) => {
    setOpen(false);
    if (id === current) return;
    // choosing series.json's own target is the same as clearing the editor's
    void setEpisodeTarget(id === null || (source === "editor" && id === series) ? null : id);
  };
  return (
    <div className="h3-pad h3-col" style={{ gap: 4, paddingTop: 0 }}>
      <div className="h3-eptarget" ref={box}>
        <button
          className="h3-btn h3-eptarget-btn"
          disabled={busy}
          aria-expanded={open}
          title={`The episode's model: every shot without a target of its own renders on it${srcLabel ? ` (${srcLabel})` : ""}. Click to change it.`}
          onClick={() => setOpen(!open)}
        >
          <span className="h3-muted">Model:</span>
          <b className="h3-ell">{targetLabel(list, current)}</b>
          <ReadinessChip r={r} />
          {srcLabel && <span className="h3-muted h3-small h3-nowrap">{srcLabel}</span>}
          <i className={busy ? "pi pi-spin pi-spinner" : "pi pi-chevron-down"} style={{ fontSize: 10 }} />
        </button>
        {open && (
          <EpisodeTargetPicker
            list={list}
            video={video}
            current={current}
            source={source}
            series={series}
            onChoose={choose}
          />
        )}
      </div>
      {known && (
        <span className="h3-small h3-muted" title="Shots with a target of their own: a shot override, or a target: line (or a profile) in the script">
          {targetCountsText(counts)}
        </span>
      )}
      {banner && (
        <div className="h3-note h3-note-err h3-row">
          <span className="h3-grow">{banner}.</span>
          <button className="h3-btn" onClick={() => openMissing(current)}>What's missing</button>
        </div>
      )}
    </div>
  );
}

function EpisodeTargetPicker({ list, video, current, source, series, onChoose }: {
  list: TargetList;
  video: Target[];
  current: string;
  source: string | null;
  series: string | null;
  onChoose: (id: string | null) => void;
}) {
  const def = listDefaultTarget(list);
  return (
    <div className="h3-pop" role="listbox">
      <div className="h3-menu-head">The episode's model, for every shot without its own</div>
      {video.map((t) => {
        const r = readinessOf(list, t.id);
        const b = readinessBadge(r);
        const on = t.id === current;
        return (
          <div key={t.id} className={`h3-pop-item${on ? " h3-on" : ""}`} role="option" aria-selected={on} onClick={() => onChoose(t.id)} title={b?.title ?? t.id}>
            <span className={`h3-rd-icon h3-rd-${b?.cls ?? "none"}`}>{b?.icon ?? ""}</span>
            <span className="h3-grow h3-col" style={{ gap: 0, minWidth: 0 }}>
              <span className="h3-ell">
                {t.label || t.id}
                {t.id === series ? <span className="h3-muted h3-small"> · series.json</span> : t.id === def && !series ? <span className="h3-muted h3-small"> · default</span> : null}
              </span>
              {r && r.status !== "ready" && (
                <span className="h3-small h3-muted">
                  {r.status === "unknown" ? "readiness unknown" : missingCountText(normReadiness(r), r.status === "not_ready" ? ["required"] : ["accelerator", "optional"])}
                  {" · "}<MissingLink target={t.id} />
                </span>
              )}
            </span>
            {on && <i className="pi pi-check" />}
          </div>
        );
      })}
      {source === "editor" && (
        <>
          <div className="h3-menu-sep" />
          <div className="h3-pop-item" role="option" aria-selected={false} onClick={() => onChoose(null)}>
            <i className="pi pi-undo h3-muted" />
            <span className="h3-grow">
              {series ? <>Use series.json's ({targetLabel(list, series)})</> : <>Use the default ({targetLabel(list, def)})</>}
            </span>
          </div>
          <div className="h3-menu-sep" />
          <div className="h3-col h3-pad-s" style={{ gap: 3 }}>
            <span className="h3-small h3-muted">
              Set in the editor only (overrides.json). To make it permanent, add this line to the <span className="h3-mono">series</span> block of series.json:
            </span>
            <div className="h3-snippet">
              <code className="h3-mono h3-grow">{seriesSnippet(current)}</code>
              <CopyButton text={seriesSnippet(current)} what="Snippet" />
            </div>
          </div>
        </>
      )}
      <div className="h3-menu-sep" />
      <div className="h3-pop-item" onClick={() => openMissing(null)}>
        <i className="pi pi-download h3-muted" />
        <span className="h3-grow">What's missing, for every target…</span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// the What's missing window
// ---------------------------------------------------------------------------

const RECT_KEY = "h3pipe.missing.rect";

function defaultMissingRect(): Rect {
  const W = window.innerWidth || 1280;
  const H = window.innerHeight || 800;
  const w = Math.min(600, Math.max(340, W - 32));
  return { x: Math.max(0, Math.round((W - w) / 2)), y: 72, w, h: Math.max(320, Math.min(620, H - 140)) };
}

export function MissingWindow() {
  const panel = useApp((s) => s.missingPanel);
  const loading = useApp((s) => s.readinessLoading);
  const { list, video, error } = useTargets();
  if (!panel) return null;
  const sel = panel.target;
  const head = (
    <>
      <i className="pi pi-download h3-muted" />
      <b>What's missing</b>
      <select
        className="h3-in"
        style={{ maxWidth: 220 }}
        value={sel ?? ""}
        title="One target's files, or every target at a glance"
        onChange={(e) => openMissing(e.target.value || null)}
      >
        <option value="">All targets</option>
        {video.map((t) => {
          const b = readinessBadge(t.readiness);
          return <option key={t.id} value={t.id}>{b ? `${b.icon} ` : ""}{t.label || t.id}</option>;
        })}
      </select>
      <span className="h3-grow" />
      <button className="h3-btn" disabled={loading} title="Check again what's installed (after a download)" onClick={() => void refreshReadiness()}>
        <i className={loading ? "pi pi-spin pi-spinner" : "pi pi-refresh"} /> Refresh
      </button>
      <button className="h3-btn h3-icon" title="Close" onClick={closeMissing}><i className="pi pi-times" /></button>
    </>
  );
  return (
    <FloatingWindow storageKey={RECT_KEY} defaultRect={defaultMissingRect} head={head} className="h3-missing-win" minW={320}>
      <div className="h3-scroll h3-pad h3-col">
        {!list && (error ? <div className="h3-note h3-note-err">Can't list the targets: {error}</div> : <div className="h3-muted">Loading…</div>)}
        {list && (sel ? <TargetMissing list={list} id={sel} /> : <AllTargets video={video} list={list} />)}
      </div>
    </FloatingWindow>
  );
}

function AllTargets({ video, list }: { video: Target[]; list: TargetList }) {
  const anyReadiness = video.some((t) => t.readiness);
  if (!anyReadiness) {
    return <div className="h3-note">This server doesn't report readiness yet (GET /h3pipe/targets?ready=1). Update the h3pipe node pack, then Refresh.</div>;
  }
  return (
    <>
      <table className="h3-table">
        <thead>
          <tr><th>Target</th><th>Status</th><th>Missing</th><th>Graph</th><th /></tr>
        </thead>
        <tbody>
          {video.map((t) => {
            const r = t.readiness ? normReadiness(t.readiness) : null;
            return (
              <tr key={t.id} onClick={() => openMissing(t.id)} title={`${t.id}: show its files`}>
                <td><span className="h3-ell">{t.label || t.id}</span></td>
                <td><ReadinessChip r={r} /></td>
                <td className="h3-small">
                  {!r ? "" : r.status === "unknown" ? "ComfyUI didn't answer" : r.status === "ready" ? <span className="h3-muted">nothing</span> : missingCountText(r)}
                </td>
                <td className="h3-small" title={t.graph?.where || ""}>{graphWord(t)}</td>
                <td><button className="h3-link" onClick={(e) => { e.stopPropagation(); openMissing(t.id); }}>details</button></td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div className="h3-small h3-muted">
        ✓ ready: every file is installed · ◐ degraded: renders, but slower or with a feature off · ✗ not ready: a needed
        file is missing, so its shots are skipped.
      </div>
      <CustomTargets list={list} />
    </>
  );
}

/** The all-targets table's Graph cell: where this target's workflow comes from. */
function graphWord(t: Target): React.ReactNode {
  const g = t.graph;
  if (!g) return "";
  const word = g.source === "env" ? `$${g.env}` :
    g.source === "comfy" ? "ComfyUI" :
    g.source === "saved" ? "$COMFYUI_PATH" :
    g.source === "repo" ? "repo" : "missing";
  return (
    <>
      <span className={g.source === "none" ? "h3-note-err" : g.source === "repo" ? "h3-muted" : ""}>{word}</span>
      {g.differs ? <span className="h3-muted"> · edited</span> : null}
    </>
  );
}

function statusLine(label: string, r: Readiness): string {
  switch (r.status) {
    case "ready":
      return `${label} has every file it needs.`;
    case "degraded":
      return pickWarning(label, r)?.text ?? `${label} renders, degraded.`;
    case "not_ready":
      return `${label} can't render yet: ${missingCountText(r, ["required"])}. Its shots are skipped until they're installed.`;
    default:
      return `ComfyUI didn't answer, so it isn't known what ${label} has. Refresh to try again.`;
  }
}

function TargetMissing({ list, id }: { list: TargetList; id: string }) {
  const t = findTarget(list, id);
  const label = targetLabel(list, id);
  if (!t) return <div className="h3-note h3-note-err">{id} isn't a target this server knows.</div>;
  if (!t.readiness) {
    return <div className="h3-note">This server didn't report {label}'s readiness (GET /h3pipe/targets?ready=1). Update the h3pipe node pack, then Refresh.</div>;
  }
  const r = normReadiness(t.readiness);
  const groups = missingGroups(r);
  const subs = resolutionNotes(Object.fromEntries(Object.entries(r.resolved).filter(([, x]) => x.how === "family")), t);
  return (
    <>
      <div className={`h3-note${r.status === "not_ready" ? " h3-note-err" : r.status === "ready" ? " h3-note-info" : ""}`}>
        <div className="h3-row">
          <ReadinessChip r={r} />
          <span className="h3-grow">{statusLine(label, r)}</span>
        </div>
      </div>
      <GraphCard t={t} />
      {groups.map((g) => (
        <div key={g.tier} className="h3-col" style={{ gap: 4 }}>
          <div className="h3-h">{g.label} <span className="h3-muted h3-small" style={{ textTransform: "none", fontWeight: 400 }}>{g.blurb}</span></div>
          {g.files.map((m) => <MissingFileCard key={`${m.param}|${m.want}`} m={m} t={t} />)}
        </div>
      ))}
      {r.nodes_missing.length > 0 && (
        <div className="h3-col" style={{ gap: 4 }}>
          <div className="h3-h">Custom nodes</div>
          {r.nodes_missing.map((n) => (
            <div key={n} className="h3-mfile">
              <div className="h3-row">
                <span className="h3-tier h3-tier-node">node</span>
                <b className="h3-mono h3-ell h3-grow" title={n}>{n}</b>
                <CopyButton text={n} what="Node name" />
              </div>
              <div className="h3-small">custom node needed: install the node pack that provides it (ComfyUI-Manager can search by node name), then restart ComfyUI.</div>
            </div>
          ))}
        </div>
      )}
      {r.features_off.length > 0 && (
        <div className="h3-col" style={{ gap: 2 }}>
          <div className="h3-h">Off until then</div>
          <ul className="h3-missing-list">{r.features_off.map((f) => <li key={f}>{f}</li>)}</ul>
        </div>
      )}
      {subs.length > 0 && (
        <div className="h3-col" style={{ gap: 2 }}>
          <div className="h3-h">Substitutions</div>
          <ul className="h3-missing-list">{subs.map((s) => <li key={s}>{s}</li>)}</ul>
        </div>
      )}
      {(groups.length > 0 || r.nodes_missing.length > 0) && (
        <div className="h3-small h3-muted">Put each file in its folder under your ComfyUI install, then click Refresh.</div>
      )}
    </>
  );
}

/**
 * Phase 11: which workflow this target renders with, and the two buttons that
 * move it: copy the repo's graph into ComfyUI to edit on the canvas, and put it
 * back. $ENV wins over both, so there both buttons are off and the variable is
 * named.
 */
function GraphCard({ t }: { t: Target }) {
  const g = t.graph;
  const busy = useApp((s) => !!s.busy[`workflow|${t.id}`]);
  if (!g) return null;
  const env = g.source === "env";
  const saved = g.source === "comfy";
  const where =
    env ? `$${g.env} points at it` :
    saved ? "saved in this ComfyUI — open it on the canvas, edit, save" :
    g.source === "saved" ? "$COMFYUI_PATH's workflows folder" :
    g.source === "repo" ? "the repo's copy (h3pipe ships it)" :
    "no workflow of that name was found: renders will fail";
  const confirmInstall = async () => {
    if (await installWorkflow(t.id)) return;
    if (!confirm(`ComfyUI already has a workflow called ${g.name}.\n\nReplace it with the repo's copy? Your edits to it are lost.`)) return;
    void installWorkflow(t.id, true);
  };
  return (
    <div className="h3-col" style={{ gap: 4 }}>
      <div className="h3-h">Graph</div>
      <div className="h3-mfile">
        <div className="h3-row">
          <span className={`h3-tier h3-tier-${g.source === "none" ? "required" : "node"}`}>
            {env ? "$ENV" : saved ? "ComfyUI" : g.source === "none" ? "missing" : "repo"}
          </span>
          <b className="h3-mono h3-ell h3-grow" title={g.where || g.name}>{g.name}</b>
          {g.differs && <span className="h3-chip" title="The graph in force isn't the repo's copy (what a job patches — model, prompt, size, seed — is ignored)">edited here</span>}
          <CopyButton text={g.name} what="Workflow name" />
        </div>
        <div className="h3-small">{where}.</div>
        {g.installed && !saved && !env && (
          <div className="h3-small h3-muted">A copy is saved in ComfyUI, but this one wins.</div>
        )}
        {g.error && <div className="h3-note h3-note-err h3-small">{g.error}</div>}
        <div className="h3-row h3-small">
          {!env && !saved && (
            <button className="h3-btn" disabled={busy || !g.repo} title={g.repo ? `Write ${g.name} into ComfyUI's workflows, to edit on the canvas` : "This target ships no workflow.json"} onClick={() => void confirmInstall()}>
              <i className={busy ? "pi pi-spin pi-spinner" : "pi pi-download"} /> Copy to ComfyUI
            </button>
          )}
          {saved && (
            <button className="h3-btn" disabled={busy} title={`Delete ComfyUI's ${g.name}: renders go back to the repo's copy`} onClick={() => void revertWorkflow(t.id)}>
              <i className={busy ? "pi pi-spin pi-spinner" : "pi pi-undo"} /> Revert to the repo's copy
            </button>
          )}
          {env && <span className="h3-muted">Unset ${g.env} to choose here.</span>}
        </div>
      </div>
    </div>
  );
}

function MissingFileCard({ m, t }: { m: MissingFile; t: Target }) {
  const dl = downloadOf(m);
  const folder = folderPath(m.folder);
  return (
    <div className="h3-mfile">
      <div className="h3-row">
        <span className={`h3-tier h3-tier-${m.tier}`}>{m.tier}</span>
        <b className="h3-mono h3-ell h3-grow" title={m.want}>{m.want}</b>
        <CopyButton text={m.want} what="File name" />
      </div>
      <div className="h3-small">{tierText(m, t)}</div>
      {folder && (
        <div className="h3-row h3-small">
          <span className="h3-muted">goes in</span>
          <span className="h3-mono h3-grow h3-ell" title={`ComfyUI/${folder}`}>ComfyUI/{folder}</span>
          <CopyButton text={folder} what="Folder" />
        </div>
      )}
      <div className="h3-small">
        {"url" in dl ? (
          <a className="h3-dl" href={dl.url} target="_blank" rel="noopener noreferrer" title={dl.url}>
            <i className="pi pi-external-link" /> Download{m.source ? <span className="h3-muted"> ({m.source})</span> : null}
          </a>
        ) : (
          <span className="h3-muted">No download link known: {searchText(m)}</span>
        )}
      </div>
      {(m.family || m.param) && (
        <div className="h3-small h3-muted">
          {m.param}{m.family ? ` · family ${m.family}` : ""}{m.family ? " (a file of the same family works too)" : ""}
        </div>
      )}
    </div>
  );
}
