// Phase 9a: the Promote dialog. Shows what the server can move from the
// overrides into the script / series config (ticked items), what has to stay
// (with the reason), and the diffs of both files; Confirm writes them.

import { useCallback, useEffect, useState } from "react";
import { afterAuthoredWrite, closePromote } from "../actions";
import { errText } from "../api";
import { api, host } from "../host";
import { codeSpans, confirmPromote, destLabel, initialChecks, scopeLabel, splitDiffNotice, tickedIds, valueText } from "../lib/promote";
import { diffStats, parseUnifiedDiff } from "../lib/source";
import { useApp } from "../store";
import type { PromotePlan } from "../types";
import { Dialog } from "./Dialogs";

/** A unified diff, coloured: + lines green, - lines red, hunk headers muted. */
export function UnifiedDiff({ diff }: { diff: string }) {
  const { notice, body } = splitDiffNotice(diff);
  const lines = parseUnifiedDiff(body);
  if (!lines.length && !notice.length) return <div className="h3-muted h3-small">No changes.</div>;
  return (
    <>
      {notice.map((n, i) => <div key={i} className="h3-note h3-small">{n}</div>)}
      <DiffRows lines={lines} />
    </>
  );
}

function DiffRows({ lines }: { lines: ReturnType<typeof parseUnifiedDiff> }) {
  if (!lines.length) return null;
  return (
    <div className="h3-udiff" role="table">
      {lines.map((l, i) => (
        <div key={i} className={`h3-dl h3-dl-${l.kind}`} role="row">
          <span className="h3-dl-n">{l.kind === "add" ? "" : l.old ?? ""}</span>
          <span className="h3-dl-n">{l.kind === "del" ? "" : l.new ?? ""}</span>
          <span className="h3-dl-sign">{l.kind === "add" ? "+" : l.kind === "del" ? "−" : ""}</span>
          <span className="h3-dl-text">{l.text || " "}</span>
        </div>
      ))}
    </div>
  );
}

/** Text with `backticked` spans as code. */
function Code({ text }: { text: string }) {
  return <>{codeSpans(text).map((p, i) => (p.code ? <code key={i} className="h3-mono h3-code-span">{p.text}</code> : p.text))}</>;
}

export interface PromoteViewProps {
  plan: PromotePlan;
  checked: Record<string, boolean>;
  onToggle: (id: string, on: boolean) => void;
  onAll: (on: boolean) => void;
  names: { script: string; series: string };
  /** open windows with unsaved edits */
  dirty?: { script: boolean; series: boolean };
  note?: string | null;
}

/** The dialog's body (presentational, for tests). */
export function PromoteView({ plan, checked, onToggle, onAll, names, dirty, note }: PromoteViewProps) {
  const ticked = tickedIds(plan, checked).length;
  const dirtyFiles = [dirty?.script && names.script, dirty?.series && names.series].filter(Boolean) as string[];
  const diffs = (["script", "series"] as const).filter((f) => plan.diffs?.[f]);
  return (
    <>
      {note && <div className="h3-note h3-note-warn">{note}</div>}
      {dirtyFiles.length > 0 && (
        <div className="h3-note h3-note-warn">
          {dirtyFiles.join(" and ")} {dirtyFiles.length > 1 ? "have" : "has"} unsaved edits in the editor. Promote writes the file on
          disk; the window will then ask whether to reload it or keep your version.
        </div>
      )}
      {plan.items.length === 0 ? (
        <div className="h3-muted">Nothing here can move into the script or series config.</div>
      ) : (
        <div className="h3-col" style={{ gap: 2 }}>
          <div className="h3-row">
            <span className="h3-h h3-grow">Promote ({ticked} of {plan.items.length})</span>
            <button className="h3-link" onClick={() => onAll(true)}>all</button>
            <button className="h3-link" onClick={() => onAll(false)}>none</button>
          </div>
          {plan.items.map((i) => (
            <label key={i.id} className="h3-promote-item">
              <input type="checkbox" checked={checked[i.id] !== false} onChange={(e) => onToggle(i.id, e.target.checked)} />
              <span className="h3-col h3-grow" style={{ gap: 1, minWidth: 0 }}>
                <span><Code text={i.summary || `${scopeLabel(i)} ${i.field} = ${valueText(i.value)}`} /></span>
                <span className="h3-small h3-muted">
                  <span className="h3-badge h3-b-override">{scopeLabel(i)}</span>{" "}
                  <span className="h3-mono">{i.field}</span> → <span className="h3-mono">{destLabel(i, names)}</span>
                </span>
              </span>
            </label>
          ))}
        </div>
      )}
      {plan.left.length > 0 && (
        <div className="h3-col" style={{ gap: 2 }}>
          <div className="h3-h">Stays in overrides.json ({plan.left.length})</div>
          {plan.left.map((l, i) => (
            <div key={i} className="h3-promote-left">
              <span className="h3-badge">{scopeLabel(l)}</span> <span className="h3-mono">{l.field}</span>
              <span className="h3-muted">: <Code text={l.reason} /></span>
            </div>
          ))}
        </div>
      )}
      {diffs.map((f) => {
        const st = diffStats(plan.diffs[f]);
        return (
          <details key={f} className="h3-promote-diff" open>
            <summary>
              <span className="h3-mono">{names[f]}</span>{" "}
              <span className="h3-dl-add-c">+{st.add}</span> <span className="h3-dl-del-c">−{st.del}</span>
            </summary>
            <UnifiedDiff diff={plan.diffs[f]} />
          </details>
        );
      })}
      {diffs.length > 0 && ticked < plan.items.length && (
        <div className="h3-small h3-muted">The diffs show every item; the unticked ones are left out when you confirm.</div>
      )}
    </>
  );
}

export function PromoteDialog() {
  const p = useApp((s) => s.promote);
  const ep = useApp((s) => s.ep);
  if (!p || !ep) return null;
  return <PromoteBody key={`${ep}|${p.shot ?? ""}`} ep={ep} shot={p.shot} />;
}

function PromoteBody({ ep, shot }: { ep: string; shot: string | null }) {
  const [plan, setPlan] = useState<PromotePlan | null>(null);
  const [checked, setChecked] = useState<Record<string, boolean>>({});
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const dirty = useApp((s) => s.sourceDirty);
  const scriptName = useApp((s) => s.episodes?.find((e) => e.ep === ep)?.script) ?? "script";

  const load = useCallback(async () => {
    setErr(null);
    try {
      const pl = await api().promotePlan(ep, shot);
      setPlan(pl);
      setChecked((prev) => initialChecks(pl, prev));
    } catch (e) {
      setErr(errText(e));
    }
  }, [ep, shot]);
  useEffect(() => {
    void load();
  }, [load]);

  const ids = plan ? tickedIds(plan, checked) : [];
  const confirm = async () => {
    if (!plan || !ids.length) return;
    setBusy(true);
    setErr(null);
    const out = await confirmPromote(api(), ep, shot, plan, ids);
    setBusy(false);
    if (out.kind === "done") {
      const n = out.result.promoted.length;
      host().toast("success", `Promoted ${n} override${n === 1 ? "" : "s"}`, out.result.left.length ? `${out.result.left.length} stay in overrides.json` : undefined);
      afterAuthoredWrite(out.result.build ?? null);
      closePromote();
    } else if (out.kind === "replanned") {
      setPlan(out.plan);
      setChecked((prev) => initialChecks(out.plan, prev));
      setNote("The script or series config changed since this plan was made. This is a fresh plan: check it and confirm again.");
    } else {
      setErr(out.message);
    }
  };

  return (
    <Dialog
      wide
      title={<>Promote overrides · {shot ?? "the whole episode"}</>}
      onClose={closePromote}
      footer={
        <>
          <span className="h3-grow h3-small h3-muted">Writes {scriptName} and series.json (the old versions go to _history/), then rebuilds.</span>
          <button className="h3-btn" onClick={closePromote}>Cancel</button>
          <button className="h3-btn h3-primary" disabled={!plan || !ids.length || busy} onClick={() => void confirm()}>
            {busy ? "Promoting…" : `Promote ${ids.length || ""}`.trim()}
          </button>
        </>
      }
    >
      {err && <div className="h3-note h3-note-err">{err} <button className="h3-link" onClick={() => void load()}>Retry</button></div>}
      {!plan && !err && <div className="h3-muted">Planning…</div>}
      {plan && (
        <PromoteView
          plan={plan}
          checked={checked}
          onToggle={(id, on) => setChecked((c) => ({ ...c, [id]: on }))}
          onAll={(on) => setChecked(Object.fromEntries(plan.items.map((i) => [i.id, on])))}
          names={{ script: scriptName, series: "series.json" }}
          dirty={dirty}
          note={note}
        />
      )}
    </Dialog>
  );
}
