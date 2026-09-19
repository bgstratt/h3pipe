// The render confirmation, shown when some of the shots to render are missing
// refs: which will queue, which will be skipped, and "render anyway". Phase 8:
// also the target for this run.

import { useEffect, useState } from "react";
import { closeRenderAsk, loadDetail, renderShots } from "../actions";
import { splitByMissingRefs } from "../lib/missingRefs";
import { findTarget, runSize, shotTarget, targetLabel } from "../lib/targets";
import { useApp } from "../store";
import { Dialog } from "./Dialogs";
import { useDetail, useStatus } from "./hooks";
import { MissingRefsNote } from "./MissingRefs";
import { TargetSelect, useTargets } from "./Targets";

export function RenderDialog() {
  const ask = useApp((s) => s.renderAsk);
  if (!ask) return null;
  return <RenderBody key={`${ask.pass}|${ask.shots.join(",")}`} />;
}

function RenderBody() {
  const ask = useApp((s) => s.renderAsk)!;
  const st = useStatus(ask.pass);
  const [allow, setAllow] = useState(false);
  const [busy, setBusy] = useState(false);
  // "" = each shot's own target
  const [target, setTarget] = useState("");
  const { list, video, seriesDefault } = useTargets();
  const one = ask.shots.length === 1 ? ask.shots[0] : null;
  const d = useDetail(one, ask.pass);
  useEffect(() => {
    if (one) void loadDetail(one, ask.pass);
  }, [one, ask.pass]);
  const oneStatus = one ? st?.shots.find((s) => s.shot === one) : undefined;
  const own = one ? shotTarget(d ?? oneStatus, seriesDefault) : null;
  const run = target || own || "";
  const { ready, blocked } = splitByMissingRefs(st?.shots ?? [], ask.shots);
  const n = allow ? ask.shots.length : ready.length;
  const submit = async () => {
    setBusy(true);
    try {
      // everything goes to the server, which skips the blocked shots itself
      // (and reports them) unless allow_missing_refs is set
      await renderShots(ask.shots, ask.redo, allow, target && target !== own ? target : null);
      closeRenderAsk();
    } finally {
      setBusy(false);
    }
  };
  let size: string;
  if (one) size = runSize(d, run, seriesDefault, list).text;
  else if (!target) size = "size and length set by each shot's target";
  else {
    const p = findTarget(list, target)?.presets?.[ask.pass];
    size = p?.width && p?.height ? `size set by the target (its ${ask.pass} preset is ${p.width}×${p.height})` : "size set by the target";
  }
  return (
    <Dialog
      title={<>{ask.title} <span className="h3-muted h3-small">{ask.pass}</span></>}
      onClose={closeRenderAsk}
      footer={
        <>
          <span className="h3-muted h3-small h3-grow">
            {ready.length} ready{blocked.length ? ` · ${blocked.length} missing refs` : ""}{run ? ` · ${targetLabel(list, run)}` : ""}
          </span>
          <button className="h3-btn" onClick={closeRenderAsk}>Cancel</button>
          <button className="h3-btn h3-primary" disabled={busy || n === 0} onClick={() => void submit()}>
            <i className={busy ? "pi pi-spin pi-spinner" : "pi pi-play"} /> Queue {n} {ask.pass} render{n === 1 ? "" : "s"}
          </button>
        </>
      }
    >
      {list && video.length > 0 && (
        <div className="h3-col" style={{ gap: 2 }}>
          <div className="h3-target-row">
            <span className="h3-h">Target for this run</span>
            <TargetSelect
              value={one ? run : target}
              list={list}
              video={video}
              extra={one ? undefined : "each shot's own target"}
              title="Sent with the render as a one-off target; the shots' own targets don't change"
              onChange={(id) => setTarget(id === own ? "" : id)}
            />
          </div>
          <span className="h3-small h3-muted">{size}</span>
        </div>
      )}
      {ready.length > 0 && (
        <div className="h3-small">
          <span className="h3-muted">Will queue: </span>
          {ready.join(", ")}
        </div>
      )}
      <MissingRefsNote blocked={blocked} allow={allow} setAllow={setAllow} />
      {!ready.length && !allow && (
        <div className="h3-muted h3-small">Every shot here is missing refs. Make them in the Refs tab, or render anyway.</div>
      )}
    </Dialog>
  );
}
