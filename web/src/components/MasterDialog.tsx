// Phase 13e: Master, from the cut menu. The plan (POST /h3pipe/master
// action "plan": what each shot of the cut is against the series recipe),
// then queue its upscales, then assemble the master into <episode>/master/.
// The plan is read again whenever the episode's status changes (an upscale
// landing), so the buttons follow the queue.
//
// Assembling takes a while (half an hour for a long episode), so it's a job
// the server keeps: the dialog asks for it on opening (GET /h3pipe/master/job)
// and follows its `h3pipe.master` events, so closing and reopening it, or
// reloading the page, shows the run still going rather than offering to start
// another one. A run `h3.py master` started is shown too, without progress,
// and asked about again every few seconds until it ends.

import { useEffect, useState } from "react";
import { errText } from "../api";
import { api } from "../host";
import { closeMaster, loadMasterJob } from "../actions";
import { clock, masterFraction, masterStageText, pictureText } from "../lib/master";
import { statusKey, useApp } from "../store";
import type { MasterPlan, MasterResult } from "../types";

type PostMode = "off" | "present" | "recipe";
import { Dialog } from "./Dialogs";
import { Progress } from "./Thumb";

const STATUS_LABEL: Record<string, string> = {
  upscale: "to upscale", post: "to post-process", ok: "ok", kept: "kept", queued: "working", gap: "gap",
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
  const job = useApp((s) => s.masterJob);
  const running = job?.state === "running";
  // Post-process: off (upscales only), present (each shot's fresh post where it has one,
  // else its upscale; nothing queued) or recipe (the series' post.master on every shot).
  // Remembered.
  const [opt, setOptState] = useState(() => {
    let post: PostMode = "off";
    try {
      const v = localStorage.getItem("h3pipe.masterPost");
      post = v === "recipe" || v === "1" ? "recipe" : v === "present" ? "present" : "off";
    } catch { /* not kept */ }
    return { conform: false, allow_gaps: false, prores: false, post };
  });
  const setOpt = (o: typeof opt) => {
    setOptState(o);
    try { localStorage.setItem("h3pipe.masterPost", o.post); } catch { /* not kept */ }
  };
  const byRecipe = opt.post === "recipe";
  // how the upscales are queued: in cut order (watching: stop at the first bad one) or
  // grouped by target (a batch: each model loads once). Remembered.
  const [order, setOrderState] = useState<"cut" | "target">(() => {
    try { return localStorage.getItem("h3pipe.masterOrder") === "target" ? "target" : "cut"; } catch { return "cut"; }
  });
  const setOrder = (o: "cut" | "target") => {
    setOrderState(o);
    try { localStorage.setItem("h3pipe.masterOrder", o); } catch { /* not kept */ }
  };

  // the master already made (and its .mov), from the last plan: ProRes from it
  const [existing, setExisting] = useState<{ output: string; mov: string | null } | null>(null);
  const [proresMade, setProresMade] = useState<string | null>(null);
  const prores = async () => {
    if (!ep) return;
    setBusy("prores");
    try {
      const r = await api().master({ ep, pass: ask.pass, action: "prores" });
      setProresMade(r.mov ?? null);
      setExisting((x) => (x ? { ...x, mov: r.mov ?? null } : x));
      setErr(null);
    } catch (e) {
      setErr(errText(e));
    } finally {
      setBusy(null);
    }
  };
  const run = async (action: "plan" | "queue" | "assemble") => {
    if (!ep) return;
    setBusy(action);
    try {
      const r = await api().master({ ep, pass: ask.pass, action, ...opt, ...(action === "queue" ? { order } : {}) });
      setPlan(r.plan);
      if (r.existing !== undefined) setExisting(r.existing ?? null);
      setErr(null);
      if (action === "assemble") setDone(r);
      if (action === "queue" && r.errors?.length) setErr(r.errors.map((e) => `${e.shot}: ${e.error}`).join("; "));
    } catch (e) {
      setErr(errText(e));
      // refused because one is already going (409 with the job): show that one
      if (action === "assemble") void loadMasterJob();
    } finally {
      setBusy(null);
    }
  };
  useEffect(() => { void run("plan"); }, [ep, st, opt.conform, opt.post]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { void loadMasterJob(); }, [ep]);
  // a run this editor didn't start sends no events: ask again until it ends
  useEffect(() => {
    if (!(running && job?.elsewhere)) return;
    const t = setInterval(() => void loadMasterJob(), 5000);
    return () => clearInterval(t);
  }, [running, job?.elsewhere]);

  const c = plan?.counts;
  const toQueue = c ? c.upscale + (c.post ?? 0) : 0;
  const canQueue = toQueue > 0 && !busy;
  const canAssemble = !!c && toQueue === 0 && c.queued === 0 && (c.gap === 0 || opt.allow_gaps) && !busy && !running;
  // what was made: this dialog's answer, else the job's (made before it was opened)
  const made = done?.output ? done
    : job?.state === "done" && job.output ? { output: job.output, mov: job.mov, report: job.report ?? undefined, titles: job.titles }
    : null;
  const pct = job ? masterFraction(job) : null;
  return (
    <Dialog
      title={<>Master the {ask.pass} cut{plan ? <span className="h3-muted h3-small"> · {plan.size[0]}×{plan.size[1]}, {plan.fit}, {plan.quality}{plan.post_mode === "present" ? ", posts where present" : plan.post ? ", post-processed" : ""}</span> : null}</>}
      onClose={closeMaster}
      footer={
        <>
          <span className="h3-muted h3-small h3-grow">
            {c ? `${c.ok + c.kept} ready · ${c.upscale} to upscale${plan?.post_mode === "recipe" || (plan?.post && !plan.post_mode) ? ` · ${c.post ?? 0} to post-process` : ""} · ${c.queued} working · ${c.gap} gap${c.gap === 1 ? "" : "s"}` : ""}
          </span>
          <button className="h3-btn" onClick={closeMaster}>Close</button>
          <button className="h3-btn" disabled={!canQueue} onClick={() => void run("queue")}
                  title={byRecipe
                    ? "Queue every shot that needs it: its upscale by the upscale recipe, its post right behind it by the post recipe"
                    : "Queue every shot that needs it by the series recipe (its target's section, its shot's own)"}>
            <i className="pi pi-arrow-up-right" /> {byRecipe ? `Queue ${toQueue} shot${toQueue === 1 ? "" : "s"}` : `Queue ${c?.upscale ?? 0} upscale${c?.upscale === 1 ? "" : "s"}`}
          </button>
          <button className="h3-btn h3-primary" disabled={!canAssemble} onClick={() => void run("assemble")}
                  title={running ? "A master of this episode is being assembled; wait for it to finish"
                    : `Assemble the master from the ${byRecipe ? "posts" : opt.post === "present" ? "posts where there are, else the upscales," : "upscales"} into the episode's master folder, with a report`}>
            {busy === "assemble" || running ? <i className="pi pi-spin pi-spinner" /> : <i className="pi pi-video" />}
            {running ? " Assembling…" : " Assemble master"}
          </button>
        </>
      }
    >
      {err && <div className="h3-error h3-small">{err}</div>}
      {running && job && (
        <div className="h3-col" style={{ gap: 2, marginBottom: 8 }}>
          <span className="h3-small">
            {masterStageText(job)}
            {!job.elsewhere && clock(job.started) ? <span className="h3-muted"> · started {clock(job.started)}</span> : null}
          </span>
          {pct != null && <Progress value={Math.round(pct * 1000)} max={1000} />}
          {job.elsewhere && <span className="h3-muted h3-small">No progress from here: this updates when it ends.</span>}
        </div>
      )}
      {!running && !err && job?.state === "failed" && job.error && (
        <div className="h3-error h3-small">The last master wasn't assembled: {job.error}</div>
      )}
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
            <label className="h3-col" style={{ gap: 2 }} title="In cut order: what's finished is the cut up to a point, so you can stop at the first shot that's off and keep everything before it. Grouped by target: each model loads once, faster for a batch left to run">
              <span className="h3-small h3-muted">Queue</span>
              <select value={order} onChange={(e) => setOrder(e.target.value as "cut" | "target")}>
                <option value="cut">In cut order</option>
                <option value="target">Grouped by target</option>
              </select>
            </label>
            <label className="h3-col" style={{ gap: 2 }}
                   title={"Off: the master from the upscales.\nWhere present: each shot's fresh post where it has one (made from the take menu's Post-process…), its upscale otherwise; nothing is post-processed.\nAll by recipe: every shot finished by the series config's post.master (SeedVR2 clean-up, motion blur); a shot that still needs its upscale gets its post queued right behind it."}>
              <span className="h3-small h3-muted">Post-process</span>
              <select value={opt.post} onChange={(e) => setOpt({ ...opt, post: e.target.value as PostMode })}>
                <option value="off">Off: upscales only</option>
                <option value="present">Where present</option>
                <option value="recipe">All by recipe</option>
              </select>
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
          {made && !running && (
            <div className="h3-small">
              Master: <code>{made.output}</code>{made.mov ? <> and <code>{made.mov}</code></> : null}; report <code>{made.report}</code>
              {!done && job?.finished ? <span className="h3-muted"> (made {clock(job.finished)})</span> : null}
              <div className="h3-muted">
                {made.titles
                  ? `With ${made.titles.intro && made.titles.outro ? "the intro and outro" : made.titles.intro ? "the intro" : "the outro"}, the episode's title drawn on`
                  : "No intro or outro: put INTRO.mp4 / OUTRO.mp4 in the show's _titles folder to have them added"}
              </div>
              {pictureText(made.titles) && <div className="h3-muted">{pictureText(made.titles)}</div>}
            </div>
          )}
          {existing && !running && (
            <div className="h3-row h3-small" style={{ gap: 8, alignItems: "center" }}>
              <span className="h3-muted h3-grow">
                {proresMade ? <>ProRes: <code>{proresMade}</code></>
                  : existing.mov ? <>ProRes: <code>{existing.mov}</code></>
                  : <>No ProRes .mov of <code>{existing.output}</code> yet</>}
              </span>
              <button className="h3-btn" disabled={!!busy} onClick={() => void prores()}
                      title="A ProRes 422 HQ .mov of the master already made, beside it (10-bit 4:2:2, PCM sound). Nothing is upscaled, post-processed or assembled again">
                {busy === "prores" ? <i className="pi pi-spin pi-spinner" /> : <i className="pi pi-file-export" />}
                {existing.mov || proresMade ? " ProRes again" : " ProRes from this master"}
              </button>
            </div>
          )}
        </div>
      )}
    </Dialog>
  );
}
