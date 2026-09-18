import { useEffect, useMemo } from "react";
import { cancelTake, openViewer, refreshEpisode, refreshQueue, select } from "../actions";
import { host } from "../host";
import { fmtWhen, tn } from "../lib/format";
import { promptOf, statusKey, useApp } from "../store";
import { PASSES, type Pass, type TakeSummary } from "../types";
import { aspectOf } from "./hooks";
import { Progress, Thumb, statusClass } from "./Thumb";

interface Row {
  pass: Pass;
  shot: string;
  take: TakeSummary;
}

export function QueueTab() {
  const ep = useApp((s) => s.ep);
  const status = useApp((s) => s.status);
  const prompts = useApp((s) => s.prompts);
  const running = useApp((s) => s.running);
  const pending = useApp((s) => s.pending);
  const progress = useApp((s) => s.progress);
  const busy = useApp((s) => s.busy);
  const episodes = useApp((s) => s.episodes);

  // both passes: a final render can be queued while you look at proxy
  useEffect(() => {
    if (!ep) return;
    const summ = episodes?.find((e) => e.ep === ep);
    for (const p of PASSES) if (!summ || summ.built[p]) void refreshEpisode(ep, p);
    void refreshQueue();
  }, [ep, episodes]);

  const rows = useMemo(() => {
    const out: Row[] = [];
    if (!ep) return out;
    for (const p of PASSES) {
      const st = status[statusKey(ep, p)];
      for (const s of st?.shots ?? []) for (const t of s.takes) if (t.status === "queued") out.push({ pass: p, shot: s.shot, take: t });
    }
    return out.sort((a, b) => (a.take.queued ?? "").localeCompare(b.take.queued ?? ""));
  }, [ep, status]);

  if (!ep) return <div className="h3-surface"><div className="h3-empty-state">Pick an episode in the h3 Shots tab.</div></div>;
  const st = status[statusKey(ep, "proxy")] ?? status[statusKey(ep, "final")];
  const aspect = aspectOf(st);

  return (
    <div className="h3-surface">
      <div className="h3-bar">
        <span className="h3-title">Queue</span>
        <span className="h3-muted h3-small">{rows.length} queued in {st?.episode ?? "this episode"}</span>
        <span className="h3-grow" />
        <button className="h3-btn h3-icon" title="Refresh" onClick={() => {
          for (const p of PASSES) void refreshEpisode(ep, p);
          void refreshQueue();
        }}><i className="pi pi-refresh" /></button>
      </div>
      <div className="h3-scroll">
        {!rows.length && <div className="h3-empty-state">Nothing queued. Render or redo a shot and it shows up here with live progress.</div>}
        {rows.map((r) => {
          const ref = { ep, pass: r.pass, shot: r.shot, take: r.take.take };
          const pid = r.take.comfy_prompt_id ?? promptOf({ prompts }, ref);
          const isRunning = !!pid && pid === running;
          const isPending = !!pid && pending.includes(pid);
          const prog = pid ? progress[pid] : undefined;
          const cancelling = !!busy[`cancel|${r.pass}|${r.shot}|${r.take.take}`];
          return (
            <div key={`${r.pass}|${r.shot}|${r.take.take}`} className="h3-take" style={{ borderBottom: "1px solid var(--h3-border)", borderRadius: 0 }}
              onClick={() => select(r.shot, r.take.take)}>
              <Thumb ep={ep} pass={r.pass} shot={r.shot} take={r.take} height={36} aspect={aspect}
                onDoubleClick={(e) => { e.stopPropagation(); if (r.take.mp4) openViewer(r.shot, r.take.take, null, "single", r.pass); }} />
              <div className="h3-take-info">
                <div className="h3-row">
                  <span className={`h3-dot ${statusClass("queued", isRunning)}`} />
                  <b>{r.shot} {tn(r.take.take)}</b>
                  <span className="h3-muted">{r.pass}</span>
                  <span className="h3-grow" />
                  <button className="h3-btn h3-danger" disabled={cancelling} onClick={(e) => {
                    e.stopPropagation();
                    if (confirm(`Cancel ${r.shot} ${tn(r.take.take)}${isRunning ? " (it's rendering now: ComfyUI will be interrupted)" : ""}?`)) void cancelTake(ref);
                  }}>
                    {cancelling ? "Cancelling…" : "Cancel"}
                  </button>
                </div>
                <div className="h3-small h3-muted">
                  {isRunning ? (prog ? `rendering · step ${prog.value}/${prog.max}` : "rendering") : isPending ? `waiting in ComfyUI's queue (#${pending.indexOf(pid!) + 1})` : pid ? "queued" : "queued (prompt id not known yet)"}
                  {r.take.queued ? ` · since ${fmtWhen(r.take.queued)}` : ""}
                </div>
                {isRunning && prog && <Progress value={prog.value} max={prog.max} />}
                <div className="h3-small h3-mono h3-ell" title={pid ?? ""}>{r.take.seed ? `seed ${r.take.seed}` : ""}{r.take.seed_source ? ` · ${r.take.seed_source}` : ""}</div>
              </div>
            </div>
          );
        })}
      </div>
      <div className="h3-bar" style={{ borderTop: "1px solid var(--h3-border)", borderBottom: 0 }}>
        <span className="h3-muted h3-small h3-grow">
          ComfyUI: {running ? "busy" : "idle"}{pending.length ? `, ${pending.length} pending` : ""}
        </span>
        <button className="h3-btn" onClick={() => host().show("shots")}>Shots</button>
      </div>
    </div>
  );
}
