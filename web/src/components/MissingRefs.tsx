// "These shots will be skipped: missing refs", with the render-anyway checkbox.
// Used by the inspector, the redo dialog and the render dialog.

import { useState } from "react";
import { showMissingRefs } from "../actions";
import { missingRefsSummary, missingRefsTitle, noAnyway } from "../lib/missingRefs";
import type { MissingRef, ShotStatus } from "../types";

export const RENDER_ANYWAY_LABEL = "Render anyway (missing pictures become flat grey — close to text-to-video)";

export function MissingRefsNote({ blocked, allow, setAllow, compact }: {
  blocked: { shot: string; refs: MissingRef[] }[];
  allow: boolean;
  setAllow: (on: boolean) => void;
  compact?: boolean;
}) {
  if (!blocked.length) return null;
  const one = blocked.length === 1;
  // shots that can't render anyway (Wan 14B I2V without a first frame) stay skipped
  const soft = blocked.filter((b) => !noAnyway(b.refs).length).length;
  return (
    <div className={`h3-note ${allow ? "h3-note-info" : ""}`}>
      <div>
        <b>{one ? `${blocked[0].shot} is missing refs` : `${blocked.length} shots are missing refs`}</b>
        {allow && soft ? ` and will render with stand-ins${soft < blocked.length ? " (except those that can't)" : ""}.` : ` and will be skipped.`}
      </div>
      <ul className="h3-missing-list">
        {blocked.slice(0, compact ? 4 : 30).map((b) => (
          <li key={b.shot} title={missingRefsTitle(b.refs)}>
            {!one && <b>{b.shot}: </b>}
            {noAnyway(b.refs).length
              ? <span>{[...new Set(noAnyway(b.refs).map((r) => r.why || `needs ${r.slot}`))].join("; ")}</span>
              : b.refs.map((r, i) => (
                <span key={i} className="h3-mono">
                  {i > 0 ? ", " : ""}{r.slot} <span className="h3-muted">{r.path}</span>
                </span>
              ))}
          </li>
        ))}
        {blocked.length > (compact ? 4 : 30) && <li className="h3-muted">…and {blocked.length - (compact ? 4 : 30)} more</li>}
      </ul>
      <div className="h3-row h3-wrap">
        {soft > 0 && (
          <label className="h3-check">
            <input type="checkbox" checked={allow} onChange={(e) => setAllow(e.target.checked)} />
            {RENDER_ANYWAY_LABEL}
          </label>
        )}
        <span className="h3-grow" />
        <button className="h3-link" onClick={() => showMissingRefs(one ? blocked[0].shot : null)}>Show in Refs</button>
      </div>
    </div>
  );
}

/** The episode-level warning in the Shots tab. */
export function MissingRefsSummary({ shots }: { shots: ShotStatus[] }) {
  const [open, setOpen] = useState(false);
  const sum = missingRefsSummary(shots);
  if (!sum.shots.length) return null;
  return (
    <div className="h3-note" title={sum.files.map((f) => `${f.path}: ${f.shots.length} shot(s)`).join("\n")}>
      <div className="h3-row">
        <i className="pi pi-exclamation-triangle" style={{ color: "var(--h3-stale)" }} />
        <span className="h3-grow"><b>{sum.text}.</b> Renders skip them unless you render anyway.</span>
        <button className="h3-link" onClick={() => setOpen(!open)}>{open ? "hide" : "details"}</button>
        <button className="h3-link" onClick={() => showMissingRefs(null)}>Refs</button>
      </div>
      {open && (
        <ul className="h3-missing-list">
          {sum.files.map((f) => (
            <li key={f.path}>
              <span className="h3-mono">{f.path}</span>{" "}
              <span className="h3-muted">({f.kind}{f.subject ? `, ${f.subject}` : ""}) blocks {f.shots.length}: {f.shots.slice(0, 8).join(", ")}{f.shots.length > 8 ? "…" : ""}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
