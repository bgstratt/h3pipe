// P10: a pass's issues — the notepad you fill while watching a proxy.
//
// Two surfaces. The **note box** is meant to be fast: `n` on the selected clip
// or **Add issue…** in its menu opens it, you type a sentence, Enter saves
// (Shift+Enter for a second line), Esc closes. The **Issues window** is the list,
// with the export to paste into an assistant.
//
// A note snapshots what produced the take, server side — so nothing here has to
// carry the script or the prompt around, and an edit to the script afterwards
// never rewrites what you wrote.

import { useEffect, useRef } from "react";
import {
  clearIssues, closeIssue, copyIssueExport, issuesOf, loadIssues, openIssues, openViewer,
  resolveIssue, saveIssue, select, setIssueText,
} from "../actions";
import { fmtWhen, tn } from "../lib/format";
import { useApp } from "../store";
import { Dialog } from "./Dialogs";

/** The note box: shot, take, one textarea, Save. */
export function IssueDialog() {
  const d = useApp((s) => s.issueDraft);
  const box = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    if (d) box.current?.focus();
  }, [d?.shot, d?.pass]);
  if (!d) return null;
  const save = () => void saveIssue();
  return (
    <Dialog
      title={<>What's wrong with {d.shot}? <span className="h3-muted h3-small">
        {d.take ? tn(d.take) : "no take yet"} · {d.pass}
      </span></>}
      onClose={closeIssue}
      footer={
        <>
          <span className="h3-muted h3-small h3-grow">
            Enter saves · Shift+Enter for a new line
          </span>
          <button className="h3-btn" onClick={closeIssue}>Cancel</button>
          <button className="h3-btn h3-primary" disabled={!!d.busy || !d.text.trim()} onClick={save}>
            <i className={d.busy ? "pi pi-spin pi-spinner" : "pi pi-check"} /> Save
          </button>
        </>
      }
    >
      <textarea
        ref={box}
        className="h3-in"
        rows={4}
        placeholder="Kell enters from the wrong side; the truck should already be in frame"
        value={d.text}
        onChange={(e) => setIssueText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            save();
          }
          // the timeline's keys must not fire while typing a note
          e.stopPropagation();
        }}
      />
      {d.error && <div className="h3-note h3-note-err">{d.error}</div>}
      <div className="h3-muted h3-small">
        It keeps the shot's script lines and the prompt that produced this take, so the export
        shows an assistant what actually caused it.
      </div>
    </Dialog>
  );
}

/** The list for this pass, and the export. */
export function IssuesWindow() {
  const open = useApp((s) => s.issuesOpen);
  const ep = useApp((s) => s.ep);
  const pass = useApp((s) => s.pass);
  const items = useApp((s) => (s.ep ? s.issues[`${s.ep}|${s.pass}`] : undefined)) ?? [];
  useEffect(() => {
    if (open && ep) void loadIssues(ep, pass);
  }, [open, ep, pass]);
  if (!open) return null;
  const live = items.filter((x) => !x.addressed);
  const done = items.filter((x) => x.addressed);
  return (
    <Dialog
      title={<>Issues <span className="h3-muted h3-small">{pass} pass</span></>}
      onClose={() => openIssues(false)}
      wide
      footer={
        <>
          <span className="h3-muted h3-small h3-grow">
            {items.length
              ? `${live.length} open${done.length ? ` · ${done.length} addressed` : ""}`
              : "Nothing noted"}
          </span>
          <button
            className="h3-btn"
            disabled={!done.length}
            title={done.length
              ? "Drop the issues whose shot has been rebuilt or re-rendered"
              : "Nothing has been addressed yet"}
            onClick={() => void clearIssues(true)}
          >
            <i className="pi pi-check-circle" /> Clear addressed{done.length ? ` (${done.length})` : ""}
          </button>
          <button
            className="h3-btn h3-primary"
            disabled={!live.length}
            title={live.length
              ? "Copy the markdown: each note with the script lines and prompt that produced it"
              : "Nothing open to export"}
            onClick={() => void copyIssueExport()}
          >
            <i className="pi pi-copy" /> Copy export
          </button>
        </>
      }
    >
      {!items.length && (
        <div className="h3-empty-state">
          Nothing noted for the {pass} pass. Select a clip and press <b>n</b>, or use a clip's
          menu, while you watch.
        </div>
      )}
      {items.map((x) => (
        <div key={x.id} className={`h3-col h3-issue${x.addressed ? " h3-muted" : ""}`} style={{ gap: 2 }}>
          <div className="h3-row h3-small">
            <button
              className="h3-link"
              title={`Select ${x.shot}`}
              onClick={() => {
                select(x.shot);
                if (x.take) openViewer(x.shot, x.take, null, undefined, x.pass);
              }}
            >
              <b>{x.shot}</b>{x.take ? ` ${tn(x.take)}` : ""}
            </button>
            {x.addressed && (
              <span className="h3-badge h3-b-ok" title="Its shot has been rebuilt or re-rendered since">
                addressed
              </span>
            )}
            <span className="h3-grow" />
            <span className="h3-muted h3-small">{fmtWhen(x.when)}</span>
            <button className="h3-btn h3-icon" title="Resolve: drop this note" onClick={() => void resolveIssue(x.id)}>
              <i className="pi pi-times" />
            </button>
          </div>
          <div className="h3-small" style={{ whiteSpace: "pre-wrap" }}>{x.note}</div>
        </div>
      ))}
      {!!live.length && (
        <div className="h3-muted h3-small">
          <b>Copy export</b> gives you one document: every open note with the script lines and the
          compiled prompt that produced that take. Paste it into an assistant, apply its edits,
          rebuild — the notes then read <b>addressed</b> and can be cleared.
        </div>
      )}
    </Dialog>
  );
}

/** The count for the toolbar button (open issues only). */
export function useIssueCount(): number {
  const ep = useApp((s) => s.ep);
  const pass = useApp((s) => s.pass);
  const items = useApp((s) => (s.ep ? s.issues[`${s.ep}|${s.pass}`] : undefined));
  void ep;
  void pass;
  return (items ?? []).filter((x) => !x.addressed).length;
}

export { issuesOf };
