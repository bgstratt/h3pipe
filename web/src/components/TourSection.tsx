// P5: a location's camera tours (POST /h3pipe/refs/tour, h3tour.py). From the
// live plate, H3 moves the camera where the move says and holds; each held
// frame lands as one of the location's tour holds. A hold is used for the
// location or one of its angles (POST /h3pipe/refs/copy-take) as a plate
// candidate, optionally sharpened. Unlike a 360, a tour can move the camera to
// a new spot, and its holds are full-size video frames.

import { useMemo, useState } from "react";
import { generateRef, loadRefs, refLabel, selectRefTake } from "../actions";
import { api, host } from "../host";
import { tn } from "../lib/format";
import { useApp } from "../store";
import type { Ref, RefTake } from "../types";
import { SHARPEN } from "./RefEdit";
import { useTargets } from "./Targets";

const MOVES: { label: string; text: string }[] = [
  { label: "Turn left", text: "The camera turns slowly to the left, about a quarter turn, and stops and holds" },
  { label: "Turn right", text: "The camera turns slowly to the right, about a quarter turn, and stops and holds" },
  { label: "Look both ways", text: "The camera turns slowly to the left and holds; then it turns back past the start to the right and holds" },
  { label: "Turn around", text: "The camera turns slowly all the way around to face the opposite direction, and stops and holds" },
  { label: "Walk in", text: "The camera moves slowly forward into the space, toward the far side, and stops and holds" },
  { label: "Pull back", text: "The camera moves slowly backward, revealing more of the space around, and stops and holds" },
  { label: "Look up", text: "The camera tilts slowly up to the ceiling or sky, and stops and holds" },
];
const SHARPEN_TARGET = "qwen_rapid_aio";

export function TourSection({ ep, r }: { ep: string; r: Ref }) {
  const refs = useApp((s) => s.refs[ep]);
  const { list } = useTargets();
  const [move, setMove] = useState("");
  const [seconds, setSeconds] = useState(6);
  const [busy, setBusy] = useState(false);
  const [sel, setSel] = useState<number | null>(null);
  const tours = r.tours ?? [];
  const holds = r.tour_holds ?? [];
  const master = r.of ?? r.id.replace(/^location:/, "");
  const places = useMemo(
    () => (refs ?? []).filter((x) => x.kind === "location" && (x.id === `location:${master}` || x.of === master)),
    [refs, master],
  );
  const [dest, setDest] = useState(r.id);
  const hold = holds.find((t) => t.take === sel);
  const running = tours.filter((t) => t.status === "queued").length;

  const run = async () => {
    setBusy(true);
    try {
      const res = await api().refsTour({ ep, ref: r.id, move, seconds, count: 1 });
      host().toast("success", `${refLabel(r.id, null)}: tour queued`, `t${res.queued.map((q) => String(q.tour).padStart(2, "0")).join(", t")}: its held frames arrive as holds when it finishes`);
      await loadRefs(ep);
    } catch (e) {
      host().toast("error", "The tour didn't queue", e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const use = async (t: RefTake, sharpen: boolean) => {
    setBusy(true);
    try {
      const made = await api().refsCopyTake({ ep, ref: r.id, view: "tour", take: t.take, to: dest });
      host().toast("success", `${refLabel(dest, null)}: ${tn(made.take)}`, `from tour hold ${tn(t.take)}; pick it to make it the plate`);
      await loadRefs(ep);
      selectRefTake(dest, null, made.take);
      if (sharpen) {
        const rapid = list?.targets.some((x) => x.id === SHARPEN_TARGET);
        await generateRef({
          ref: dest, view: null, count: 1, seed_mode: "new", seed: null, prompt: SHARPEN, model: null,
          loras: null, steps: null, note: "sharpen a tour hold", target: rapid ? SHARPEN_TARGET : null,
          edit: { take: made.take, with: [], wrap: true },
        });
      }
    } catch (e) {
      host().toast("error", "Couldn't use the hold", e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="h3-col" style={{ gap: 4 }}>
      <div className="h3-row h3-wrap" style={{ gap: 3 }}>
        <span className="h3-h h3-small" title="A camera move through this place on H3, from its live plate: every place the camera holds becomes a hold, a full-size new angle of the same place">Tour</span>
        {MOVES.map((m) => (
          <button key={m.label} className={`h3-btn h3-small${move === m.text ? " h3-on" : ""}`} title={m.text} onClick={() => setMove(m.text)}>{m.label}</button>
        ))}
      </div>
      <textarea
        className="h3-in"
        rows={2}
        value={move}
        placeholder="Where the camera goes and where it holds, e.g. “The camera turns slowly to the left past the pillar to the parking lot and holds”"
        onChange={(e) => setMove(e.target.value)}
      />
      <div className="h3-row h3-wrap" style={{ gap: 6 }}>
        <label className="h3-small h3-row" style={{ gap: 3 }} title="How long the video runs: keep the move early and short (frames soften as a clip runs)">
          seconds
          <input className="h3-in h3-mono" style={{ width: 52 }} type="number" min={2} max={10} value={seconds} onChange={(e) => setSeconds(Number(e.target.value) || 6)} />
        </label>
        <button className="h3-btn" disabled={busy || !move.trim() || !r.exists} title={r.exists ? "Queue the tour on H3 (about a minute); its holds arrive here" : "Pick or import a plate first"} onClick={() => void run()}>
          <i className={busy ? "pi pi-spin pi-spinner" : "pi pi-video"} /> Run tour
        </button>
        {running > 0 && <span className="h3-small h3-muted"><i className="pi pi-spin pi-spinner" /> {running} running</span>}
        {tours.filter((t) => t.status === "failed").slice(-1).map((t) => (
          <span key={t.tour} className="h3-small h3-err" title={t.error}>t{String(t.tour).padStart(2, "0")} failed</span>
        ))}
      </div>
      {holds.length > 0 && (
        <div className="h3-row h3-wrap" style={{ gap: 4 }}>
          {[...holds].reverse().map((t) => (
            <button
              key={t.take}
              className={`h3-pano-thumb${sel === t.take ? " h3-on" : ""}`}
              disabled={t.status !== "ok" || !t.image}
              title={`${tn(t.take)}${t.note ? `: ${t.note}` : ""}`}
              onClick={() => setSel(sel === t.take ? null : t.take)}
              style={{
                width: 112, height: 64, padding: 0,
                border: sel === t.take ? "2px solid var(--h3-accent, #4af)" : "1px solid var(--h3-border, #444)",
                background: t.image && t.status === "ok" ? `center / cover url("${api().fileUrl(ep, t.image)}")` : undefined,
              }}
            >
              <span className="h3-thumb-label">{tn(t.take)}</span>
            </button>
          ))}
        </div>
      )}
      {hold && (
        <div className="h3-row h3-wrap" style={{ gap: 6 }}>
          <span className="h3-small h3-muted">Use hold {tn(hold.take)} for</span>
          <select className="h3-in" value={dest} onChange={(e) => setDest(e.target.value)} title="The location, or an angle of it, the hold becomes a plate candidate of. To make a new angle, add it to series.json with `of` first.">
            {places.map((x) => <option key={x.id} value={x.id}>{`${x.name}${x.of ? " (angle)" : ""}`}</option>)}
          </select>
          <button className="h3-btn" disabled={busy} title="Copy the hold in as a new candidate (not picked)" onClick={() => void use(hold, false)}>
            <i className="pi pi-copy" /> Use
          </button>
          <button className="h3-btn" disabled={busy} title="Copy it in, then queue the Sharpen edit on it (Rapid AIO when installed)" onClick={() => void use(hold, true)}>
            <i className="pi pi-sparkles" /> Use and sharpen
          </button>
        </div>
      )}
    </div>
  );
}
