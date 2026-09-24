// P8: supplying references in bulk. Files dropped on the Refs tab (or picked
// with the button) are matched to slots by name on the server
// (POST /h3pipe/refs/match) and shown here before anything is sent: a picture
// routed to the wrong slot means a render with the wrong character in it, so
// the guess is always on screen first.

import { closeSupply, refLabel, runSupply } from "../actions";
import { viewLabel } from "../lib/refs";
import { sharedWarning } from "../lib/shared";
import { useApp } from "../store";
import { Dialog } from "./Dialogs";

export function SupplyDialog() {
  const s = useApp((st) => st.supply);
  const ep = useApp((st) => st.ep);
  const refs = useApp((st) => (st.ep ? st.refs[st.ep] : undefined));
  if (!s) return null;
  const m = s.match;
  const busy = !!s.busy;
  const doneBy = new Map(s.done.map((d) => [d.file, d]));
  const n = m?.matched.length ?? 0;
  // P9: which of these overwrite a file other episodes read (and which of those
  // nothing has a candidate for) -- said once, not per file
  const shared = (m?.matched ?? []).flatMap((x) => {
    const r = refs?.find((y) => y.id === x.ref);
    const w = r ? sharedWarning(r, ep, "pick") : null;
    return w ? [`${r!.name}${w.gone ? " (nothing has a copy)" : ""}`] : [];
  });
  return (
    <Dialog
      title={<>Supply references <span className="h3-muted h3-small">{s.files.length} file{s.files.length === 1 ? "" : "s"}</span></>}
      onClose={closeSupply}
      wide
      footer={
        <>
          <span className="h3-muted h3-small h3-grow">
            {!m ? "Matching…"
              : busy ? `Uploading ${s.busy}…`
              : s.done.length ? `${s.done.filter((d) => d.ok).length} of ${s.done.length} went in`
              : `${n} matched · ${m.unmatched.length} left alone`}
          </span>
          <button className="h3-btn" onClick={closeSupply}>{s.done.length && !busy ? "Close" : "Cancel"}</button>
          <button
            className="h3-btn h3-primary"
            disabled={!m || !n || busy || !!s.done.length}
            title={n ? "Each file becomes a take of its slot and goes live" : "Nothing matched a slot"}
            onClick={() => void runSupply()}
          >
            <i className={busy ? "pi pi-spin pi-spinner" : "pi pi-upload"} /> Supply {n || ""}
          </button>
        </>
      }
    >
      {s.error && <div className="h3-note h3-note-err">{s.error}</div>}
      {!m && !s.error && <div className="h3-empty-state"><i className="pi pi-spin pi-spinner" /> Working out where these go…</div>}
      {m && (
        <>
          {n > 0 && (
            <div className="h3-col" style={{ gap: 2 }}>
              <div className="h3-h">These go in, and go live</div>
              {m.matched.map((x) => {
                const d = doneBy.get(x.file);
                return (
                  <div key={x.file} className="h3-row h3-small">
                    <i className={d ? (d.ok ? "pi pi-check h3-ok" : "pi pi-times h3-err")
                                    : s.busy === x.file ? "pi pi-spin pi-spinner"
                                    : x.audio ? "pi pi-volume-up h3-muted" : "pi pi-image h3-muted"} />
                    <span className="h3-mono h3-ell" style={{ flex: "0 0 40%" }} title={x.file}>{x.file}</span>
                    <span className="h3-grow h3-ell" title={x.why}>
                      → {refLabel(x.ref, null)}{x.view ? ` · ${viewLabel(x.view)}` : ""}
                      {d?.why ? <span className="h3-err"> {d.why}</span> : null}
                    </span>
                  </div>
                );
              })}
            </div>
          )}
          {m.unmatched.length > 0 && (
            <div className="h3-col" style={{ gap: 2 }}>
              <div className="h3-h">Left alone</div>
              {m.unmatched.map((x) => (
                <div key={x.file} className="h3-row h3-small h3-muted">
                  <i className="pi pi-minus-circle" />
                  <span className="h3-mono h3-ell" style={{ flex: "0 0 40%" }} title={x.file}>{x.file}</span>
                  <span className="h3-grow h3-ell" title={x.why}>{x.why}</span>
                </div>
              ))}
            </div>
          )}
          {!n && !m.unmatched.length && <div className="h3-empty-state">Nothing to supply.</div>}
          {shared.length > 0 && !s.done.length && (
            <div className="h3-note h3-note-info h3-small">
              {shared.length === 1
                ? `1 of these replaces a live file other episodes read (${shared[0]}).`
                : `${shared.length} of these replace live files other episodes read.`}
              {" "}Their takes of those shots go <b>stale: ref</b>, and Re-render stale offers them.
              {shared.some((x) => x.endsWith("(nothing has a copy)"))
                && " One has no candidate behind it at all — check before supplying."}
            </div>
          )}
          {n > 0 && !s.done.length && (
            <div className="h3-muted h3-small">
              Each file becomes a take of its slot, picked so it is the live file — the same as
              dropping it on that row. Names are matched against the file each ref already names
              in the series config, its id, and a view's tag or word.
            </div>
          )}
        </>
      )}
    </Dialog>
  );
}
