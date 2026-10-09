// P5: a location's 360 panoramas. "Make 360" (POST /h3pipe/refs/generate with
// `pano`) turns the live plate into an equirectangular 360 with the Qwen
// pano360 LoRA; the viewer aims a camera into one (drag to turn, wheel to
// zoom) and saves that view as a plate candidate of the location or one of its
// angles. A 360 spreads its pixels round the whole circle, so a cut view is
// soft: "Save and sharpen" queues an edit of the saved candidate that redraws
// it sharp (Rapid AIO when installed).

import { useEffect, useMemo, useRef, useState, type PointerEvent, type WheelEvent } from "react";
import { closePano, generateRef, openPano, uploadRef } from "../actions";
import { api } from "../host";
import { tn } from "../lib/format";
import { clampAim, renderView, viewDetail, type PanoAim, type Rgba } from "../lib/pano";
import { store, useApp } from "../store";
import type { Ref } from "../types";
import { FloatingWindow, type Rect } from "./FloatingWindow";
import { SHARPEN } from "./RefEdit";
import { useTargets } from "./Targets";

const RECT_KEY = "h3pipe.pano.rect";
const SIZES = ["1344x768", "1536x640", "1024x1024", "768x1344"];
const PREVIEW_W = 640;
const SHARPEN_TARGET = "qwen_rapid_aio";

function defaultRect(): Rect {
  const W = window.innerWidth || 1280;
  const H = window.innerHeight || 800;
  const w = Math.min(760, Math.max(360, W - 32));
  return { x: Math.max(0, Math.round((W - w) / 2)), y: 60, w, h: Math.max(420, Math.min(680, H - 100)) };
}

/** The 360 row of a location: Make 360, and its panoramas to open. */
const PANO_SIZES = ["2048x1024", "1536x768"];

export function PanoSection({ ep, r }: { ep: string; r: Ref }) {
  const busy = useApp((s) => !!s.busy[`refgen|${r.id}`]);
  const [psize, setPsize] = useState(PANO_SIZES[0]);
  const panos = r.panos ?? [];
  return (
    <div className="h3-col" style={{ gap: 3 }}>
      <div className="h3-row h3-wrap" style={{ gap: 6 }}>
        <span className="h3-h h3-small" title="A 360 of this place, to aim a camera into and save new angles from">360</span>
        <button
          className="h3-btn"
          disabled={busy || !r.exists}
          title={r.exists ? "Turn the live plate into a 360 panorama (Qwen 2.1 + the pano360 LoRA). Open it to aim a camera and save a view as a new angle." : "Pick or import a plate first"}
          onClick={() => void generateRef({
            ref: r.id, view: null, count: 1, seed_mode: "new", seed: null, prompt: null, model: null,
            loras: null, steps: null, note: "360", pano: {}, size: psize,
          })}
        >
          <i className={busy ? "pi pi-spin pi-spinner" : "pi pi-globe"} /> Make 360
        </button>
        <select className="h3-in" value={psize} onChange={(e) => setPsize(e.target.value)} title="The 360's size: 2048x1024 has a third more detail in every view; 1536x768 is about twice as fast">
          {PANO_SIZES.map((x) => <option key={x} value={x}>{x}</option>)}
        </select>
        {!panos.length && <span className="h3-small h3-muted">none yet</span>}
      </div>
      {panos.length > 0 && (
        <div className="h3-row h3-wrap" style={{ gap: 4 }}>
          {[...panos].reverse().map((t) => (
            <button
              key={t.take}
              className="h3-pano-thumb"
              disabled={t.status !== "ok" || !t.image}
              title={t.status === "ok" ? `Open 360 ${tn(t.take)}: aim a camera and save a view` : t.status}
              onClick={() => openPano(r.id, t.take)}
              style={{
                width: 128, height: 64, padding: 0, border: "1px solid var(--h3-border, #444)",
                background: t.image && t.status === "ok" ? `center / cover url("${api().fileUrl(ep, t.image)}")` : undefined,
              }}
            >
              <span className="h3-thumb-label">{tn(t.take)}{t.status !== "ok" ? ` ${t.status}` : ""}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export function PanoWindow() {
  const p = useApp((s) => s.panoView);
  const ep = useApp((s) => s.ep);
  if (!p || !ep) return null;
  return <PanoBody key={`${p.ref}|${p.take}`} ep={ep} refId={p.ref} take={p.take} />;
}

function PanoBody({ ep, refId, take }: { ep: string; refId: string; take: number }) {
  const refs = useApp((s) => s.refs[ep]);
  const { list } = useTargets();
  const r = refs?.find((x) => x.id === refId);
  const t = r?.panos?.find((x) => x.take === take);
  const url = t?.image ? api().fileUrl(ep, t.image) : null;
  const [src, setSrc] = useState<Rgba | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [aim, setAim] = useState<PanoAim>({ yaw: 0, pitch: 0, fov: 70 });
  const [size, setSize] = useState(SIZES[0]);
  const [busy, setBusy] = useState(false);
  const canvas = useRef<HTMLCanvasElement>(null);
  const drag = useRef<{ x: number; y: number; aim: PanoAim } | null>(null);
  const [ow, oh] = size.split("x").map(Number);
  const pw = PREVIEW_W;
  const ph = Math.round((PREVIEW_W * oh) / ow);

  // the place and its angles: where a view can be saved
  const master = r?.kind === "location" ? (r.of ?? r.id.replace(/^location:/, "")) : null;
  const places = useMemo(
    () => (refs ?? []).filter((x) => x.kind === "location" && master != null
      && (x.id === `location:${master}` || x.of === master)),
    [refs, master],
  );
  const [dest, setDest] = useState(refId);

  // the panorama's pixels, once
  useEffect(() => {
    if (!url) return;
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.onload = () => {
      const c = document.createElement("canvas");
      c.width = img.naturalWidth;
      c.height = img.naturalHeight;
      const g = c.getContext("2d");
      if (!g) return setErr("This browser can't read the picture");
      g.drawImage(img, 0, 0);
      const d = g.getImageData(0, 0, c.width, c.height);
      setSrc({ width: d.width, height: d.height, data: d.data });
    };
    img.onerror = () => setErr("Couldn't load the 360");
    img.src = url;
  }, [url]);

  // the preview, redrawn as the aim changes
  useEffect(() => {
    const c = canvas.current;
    if (!c || !src) return;
    const id = requestAnimationFrame(() => {
      const g = c.getContext("2d");
      if (!g) return;
      const out = g.createImageData(pw, ph);
      renderView(src, out, aim);
      g.putImageData(out, 0, 0);
    });
    return () => cancelAnimationFrame(id);
  }, [src, aim, pw, ph]);

  const down = (e: PointerEvent<HTMLCanvasElement>) => {
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
    drag.current = { x: e.clientX, y: e.clientY, aim };
  };
  const move = (e: PointerEvent<HTMLCanvasElement>) => {
    const d = drag.current;
    if (!d) return;
    const k = d.aim.fov / (canvas.current?.clientWidth || pw);
    setAim(clampAim({ ...d.aim, yaw: d.aim.yaw - (e.clientX - d.x) * k, pitch: d.aim.pitch + (e.clientY - d.y) * k }));
  };
  const up = () => { drag.current = null; };
  const wheel = (e: WheelEvent<HTMLCanvasElement>) => {
    setAim((a) => clampAim({ ...a, fov: a.fov * (1 + e.deltaY * 0.001) }));
  };

  const save = async (sharpen: boolean) => {
    if (!src) return;
    setBusy(true);
    try {
      const c = document.createElement("canvas");
      c.width = ow;
      c.height = oh;
      const g = c.getContext("2d");
      if (!g) throw new Error("This browser can't draw the view");
      const out = g.createImageData(ow, oh);
      renderView(src, out, aim);
      g.putImageData(out, 0, 0);
      const blob = await new Promise<Blob | null>((ok) => c.toBlob(ok, "image/png"));
      if (!blob) throw new Error("Couldn't encode the view");
      const name = `${dest.replace(/^location:/, "")}_360t${take}_y${Math.round(aim.yaw)}_p${Math.round(aim.pitch)}_f${Math.round(aim.fov)}.png`;
      const ok = await uploadRef(dest, null, new File([blob], name, { type: "image/png" }), false);
      if (ok && sharpen) {
        const sel = store.get().refSel;
        if (sel && sel.ref === dest) {
          const rapid = list?.targets.some((x) => x.id === SHARPEN_TARGET);
          await generateRef({
            ref: dest, view: null, count: 1, seed_mode: "new", seed: null, prompt: SHARPEN, model: null,
            loras: null, steps: null, note: "sharpen a 360 view", target: rapid ? SHARPEN_TARGET : null,
            edit: { take: sel.take, with: [], wrap: true },
          });
        }
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const detail = src ? viewDetail(aim, ow, src.width) : 1;
  const head = (
    <>
      <i className="pi pi-globe h3-muted" />
      <b className="h3-ell">{r?.name ?? refId} · 360 {tn(take)}</b>
      <span className="h3-grow" />
      <button className="h3-btn h3-icon" title="Close" onClick={closePano}><i className="pi pi-times" /></button>
    </>
  );
  return (
    <FloatingWindow storageKey={RECT_KEY} defaultRect={defaultRect} head={head} className="h3-pano-win" minW={360} minH={360}>
      <div className="h3-scroll h3-pad h3-col" style={{ gap: 6 }}>
        {!t && <div className="h3-note h3-note-err">That 360 is gone.</div>}
        {err && <div className="h3-note h3-note-err">{err}</div>}
        <canvas
          ref={canvas}
          width={pw}
          height={ph}
          style={{ width: "100%", aspectRatio: `${pw} / ${ph}`, cursor: "grab", touchAction: "none", background: "#000" }}
          onPointerDown={down}
          onPointerMove={move}
          onPointerUp={up}
          onPointerCancel={up}
          onWheel={wheel}
          title="Drag to turn the camera; the wheel zooms"
        />
        {!src && !err && <div className="h3-muted h3-small">Loading the 360…</div>}
        <div className="h3-row h3-wrap" style={{ gap: 8 }}>
          {(["yaw", "pitch", "fov"] as const).map((k) => (
            <label key={k} className="h3-small h3-row" style={{ gap: 3 }} title={k === "fov" ? "Field of view: wider sees more and is softer" : k === "yaw" ? "Turn left / right" : "Tilt up / down"}>
              {k === "fov" ? "FOV" : k[0].toUpperCase() + k.slice(1)}
              <input
                className="h3-in h3-mono"
                style={{ width: 64 }}
                type="number"
                value={Math.round(aim[k])}
                onChange={(e) => setAim(clampAim({ ...aim, [k]: Number(e.target.value) || 0 }))}
              />
            </label>
          ))}
          <select className="h3-in" value={size} onChange={(e) => setSize(e.target.value)} title="The saved plate's size">
            {SIZES.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </div>
        <div className={`h3-small ${detail < 0.6 ? "h3-warn" : "h3-muted"}`}>
          Detail {detail.toFixed(2)}× — {detail < 0.6
            ? "soft at this size: save and sharpen, or narrow the field of view"
            : "close to full detail"}
        </div>
        <div className="h3-row h3-wrap" style={{ gap: 6 }}>
          <span className="h3-small h3-muted">Save as a candidate of</span>
          <select className="h3-in" value={dest} onChange={(e) => setDest(e.target.value)} title="The location (or angle of it) the view becomes a plate candidate for. To make a new angle, add it to series.json with `of` first.">
            {places.map((x) => <option key={x.id} value={x.id}>{`${x.name}${x.of ? " (angle)" : ""}`}</option>)}
          </select>
          <button className="h3-btn" disabled={!src || busy} title="Save this view as a new candidate (not picked)" onClick={() => void save(false)}>
            <i className={busy ? "pi pi-spin pi-spinner" : "pi pi-save"} /> Save view
          </button>
          <button className="h3-btn h3-primary" disabled={!src || busy} title="Save this view, then queue an edit that redraws it sharp (Rapid AIO when installed)" onClick={() => void save(true)}>
            <i className="pi pi-sparkles" /> Save and sharpen
          </button>
        </div>
      </div>
    </FloatingWindow>
  );
}
