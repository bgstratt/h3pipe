// Phase 13e: Master, from the cut menu. The plan (POST /h3pipe/master
// action "plan": what each shot of the cut is against the series recipe),
// then queue its upscales, then assemble the master into <episode>/master/.
// The plan is read again whenever the episode's status changes (an upscale
// landing), so the buttons follow the queue.

import { useEffect, useState } from "react";
import { errText } from "../api";
import { api } from "../host";
import { closeMaster } from "../actions";
import { statusKey, useApp } from "../store";
import type { MasterPlan, MasterResult } from "../types";
import { Dialog } from "./Dialogs";

const STATUS_LABEL: Record<string, string> = {
  upscale: "to upscale", ok: "ok", kept: "kept", queued: "upscaling", gap: "gap",
};

export function MasterDialog() {
  const ask = useApp((s) => s.masterAsk);
  if (!ask) return null;
  return <MasterBody key={ask.pass} />;
}

function MasterBody() {
  const ask = useApp((s) => s.masterAsk)!;
  const ep = useApp((s) => s.ep);
  // the episode's status: it changes as upscales land, and the plan is read again
  const st = useApp((s) => (s.ep ? s.status[statusKey(s.ep, ask.pass)] : undefined));
  const [plan, setPlan] = useState<MasterPlan | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [done, setDone] = useState<MasterResult | null>(null);
  const [opt, setOpt] = useState({ conform: false, allow_gaps: false, prores: false });

  const run = async (action: "plan" | "queue" | "assemble") => {
    if (!ep) return;
    setBusy(action);
    try {
      const r = await api().master({ ep, pass: ask.pass, action, ...opt });
      setPlan(r.plan);
      setErr(null);
      if (action === "assemble") setDone(r);
      if (action === "queue" && r.errors?.length) setErr(r.errors.map((e) => `${e.shot}: ${e.error}`).join("; "));
    } catch (e) {
      setErr(errText(e));
    } finally {
      setBusy(null);
    }
  };
  useEffect(() => { void run("plan"); }, [ep, st, opt.conform]); // eslint-disable-line react-hooks/exhaustive-deps

  const c = plan?.counts;
  const canQueue = !!c && c.upscale > 0 && !busy;
  const canAssemble = !!c && c.upscale === 0 && c.queued === 0 && (c.gap === 0 || opt.allow_gaps) && !busy;
  return (
    <Dialog
      title={<>Master the {ask.pass} cut{plan ? <span className="h3-muted h3-small"> · {plan.size[0]}×{plan.size[1]}, {plan.fit}, {plan.quality}</span> : null}</>}
      onClose={closeMaster}
      footer={
        <>
          <span className="h3-muted h3-small h3-grow">
            {c ? `${c.ok + c.kept} ready · ${c.upscale} to upscale · ${c.queued} upscaling · ${c.gap} gap${c.gap === 1 ? "" : "s"}` : ""}
          </span>
          <button className="h3-btn" onClick={closeMaster}>Close</button>
          <button className="h3-btn" disabled={!canQueue} onClick={() => void run("queue")}
                  title="Queue every shot that needs it by the series recipe (its target's section, its shot's own)">
            <i className="pi pi-arrow-up-right" /> Queue {c?.upscale ?? 0} upscale{c?.upscale === 1 ? "" : "s"}
          </button>
          <button className="h3-btn h3-primary" disabled={!canAssemble} onClick={() => void run("assemble")}
                  title="Assemble the master from the upscales into the episode's master folder, with a report">
            {busy === "assemble" ? <i className="pi pi-spin pi-spinner" /> : <i className="pi pi-video" />} Assemble master
          </button>
        </>
      }
    >
      {err && <div className="h3-error h3-small">{err}</div>}
      {!plan && !err && <div className="h3-muted h3-small"><i className="pi pi-spin pi-spinner" /> Planning…</div>}
      {plan && (
        <div className="h3-col" style={{ gap: 8 }}>
          <div className="h3-row" style={{ gap: 12, flexWrap: "wrap" }}>
            <label className="h3-check" title="Also redo upscales made with other settings than the series recipe's (never one marked Keep)">
              <input type="checkbox" checked={opt.conform} onChange={(e) => setOpt({ ...opt, conform: e.target.checked })} />
              Conform to the recipe
            </label>
            <label className="h3-check" title="Master a cut with gaps: their clips are scaled up from their takes, and the report says so">
              <input type="checkbox" checked={opt.allow_gaps} onChange={(e) => setOpt({ ...opt, allow_gaps: e.target.checked })} />
              Allow gaps
            </label>
            <label className="h3-check" title="Also write a ProRes 422 HQ .mov beside the master, for an editor downstream">
              <input type="checkbox" checked={opt.prores} onChange={(e) => setOpt({ ...opt, prores: e.target.checked })} />
              ProRes too
            </label>
          </div>
          <div style={{ maxHeight: 320, overflow: "auto" }}>
            <table className="h3-small" style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr><th align="left">shot</th><th align="left">take</th><th align="left">status</th><th align="left">recipe</th><th align="left">note</th></tr>
              </thead>
              <tbody>
                {plan.rows.map((r) => (
                  <tr key={r.shot} className={r.status === "gap" ? "h3-error" : r.status === "ok" ? "" : "h3-muted"}>
                    <td>{r.shot}</td>
                    <td>{r.take ?? ""}</td>
                    <td>{STATUS_LABEL[r.status] ?? r.status}</td>
                    <td>{r.recipe}</td>
                    <td>{r.why}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {done?.output && (
            <div className="h3-small">
              Master: <code>{done.output}</code>{done.mov ? <> and <code>{done.mov}</code></> : null}; report <code>{done.report}</code>
            </div>
          )}
        </div>
      )}
    </Dialog>
  );
}
