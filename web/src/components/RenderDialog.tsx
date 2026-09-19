// The render confirmation, shown when some of the shots to render are missing
// refs: which will queue, which will be skipped, and "render anyway".

import { useState } from "react";
import { closeRenderAsk, renderShots } from "../actions";
import { splitByMissingRefs } from "../lib/missingRefs";
import { useApp } from "../store";
import { Dialog } from "./Dialogs";
import { useStatus } from "./hooks";
import { MissingRefsNote } from "./MissingRefs";

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
  const { ready, blocked } = splitByMissingRefs(st?.shots ?? [], ask.shots);
  const n = allow ? ask.shots.length : ready.length;
  const submit = async () => {
    setBusy(true);
    try {
      // everything goes to the server, which skips the blocked shots itself
      // (and reports them) unless allow_missing_refs is set
      await renderShots(ask.shots, ask.redo, allow);
      closeRenderAsk();
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog
      title={<>{ask.title} <span className="h3-muted h3-small">{ask.pass}</span></>}
      onClose={closeRenderAsk}
      footer={
        <>
          <span className="h3-muted h3-small h3-grow">
            {ready.length} ready{blocked.length ? ` · ${blocked.length} missing refs` : ""}
          </span>
          <button className="h3-btn" onClick={closeRenderAsk}>Cancel</button>
          <button className="h3-btn h3-primary" disabled={busy || n === 0} onClick={() => void submit()}>
            <i className={busy ? "pi pi-spin pi-spinner" : "pi pi-play"} /> Queue {n} {ask.pass} render{n === 1 ? "" : "s"}
          </button>
        </>
      }
    >
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
