// Phase 13: the Upscale dialog, opened from a take's menu (one take, either
// pass) or the cut menu (that pass's whole cut). What it offers comes from
// GET /h3pipe/upscale/options: the pixel method's models on this ComfyUI, and
// which targets have a latent upscale (and whether it's ready here).

import { useEffect, useState } from "react";
import { errText } from "../api";
import { api } from "../host";
import { closeUpscale, upscale, upscaleRequestOf, type UpscaleForm } from "../actions";
import { statusKey, useApp } from "../store";
import { SCALES, methodFor, thenSize, upscaleSize, type SizeCheck } from "../lib/upscale";
import type { TakeSummary, UpscaleOptions } from "../types";
import { Dialog } from "./Dialogs";

const DETAIL_LABELS = [
  "Keep the take (default): a light pass, a speaking mouth stays as it was",
  "More detail: one step earlier",
  "Most detail: two steps earlier; can change expressions, check dialogue shots",
];

export function UpscaleDialog() {
  const ask = useApp((s) => s.upscaleAsk);
  if (!ask) return null;
  return <UpscaleBody key={`${ask.title}|${(ask.takes ?? []).map((t) => `${t.shot}:${t.take}`).join(",")}`} />;
}

function UpscaleBody() {
  const ask = useApp((s) => s.upscaleAsk)!;
  const ep = useApp((s) => s.ep);
  const st = useApp((s) => (s.ep ? s.status[statusKey(s.ep, ask.pass)] : undefined));
  const [opts, setOpts] = useState<UpscaleOptions | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [f, setF] = useState<UpscaleForm>({ method: "auto", pixelModel: null, detail: 0, redo: !!ask.redo, vae: false,
                                              scale: 2, thenModel: null, thenScale: 2, fromUpscale: false,
                                              encoder: "auto", precision: "fp16",
                                              frequencySplit: true, keepSoft: 0, grain: 0 });
  const set = (p: Partial<UpscaleForm>) => setF((x) => ({ ...x, ...p }));

  useEffect(() => {
    let live = true;
    api().upscaleOptions()
      .then((o: UpscaleOptions) => { if (live) { setOpts(o); setF((x) => ({ ...x, pixelModel: x.pixelModel ?? o.pixel.default })); } })
      .catch((e: unknown) => { if (live) setErr(errText(e)); });
    return () => { live = false; };
  }, []);

  // the targets of the takes this would upscale
  const shots = st?.shots ?? [];
  const takes = ask.takes
    ? ask.takes.map((r) => shots.find((s) => s.shot === r.shot)?.takes.find((t) => t.take === r.take))
    : shots.filter((s) => s.cut.usable && !s.cut.placeholder).map((s) => s.takes.find((t) => t.take === s.cut.take));
  const targets = [...new Set(takes.filter(Boolean).map((t) => t!.target || "minimax_h3_ref2va"))];
  const latentOf = (id: string) => opts?.latent[id] ?? null;
  const latentAll = !!opts && targets.length > 0 && targets.every((id) => latentOf(id)?.status === "ready");
  const latentSome = !!opts && targets.some((id) => latentOf(id)?.status === "ready");
  const noLatent = targets.filter((id) => !latentOf(id));
  const pixelReady = opts?.pixel.status === "ready";
  const sv2Ready = opts?.seedvr2?.status === "ready";
  // Wan's re-sample starts by running the frames through an upscale model
  const refineModel = targets.some((id) => latentOf(id)?.mode === "pixel_refine");
  const count = ask.takes ? ask.takes.length : takes.filter(Boolean).length;

  // what each take would come out as, at a scale (and the then-pixel step after a re-sample)
  const maxScale = opts?.max_scale ?? 4;
  const real = takes.filter(Boolean) as TakeSummary[];
  const plan = (t: TakeSummary, scale: number, withThen: boolean): { method: "latent" | "pixel" | "seedvr2"; first: SizeCheck; out: SizeCheck } => {
    const li = latentOf(t.target || "minimax_h3_ref2va");
    const m = methodFor(f.method, li);
    // on top of its upscale: that upscale's size is what gets scaled
    const onUp = (f.method === "pixel" || f.method === "seedvr2") && f.fromUpscale;
    const base = onUp ? { width: t.upscale?.width, height: t.upscale?.height } : t;
    const first = f.method === "latent" && !li
      ? { ok: false, why: "its target has no re-sample" }
      : onUp && !(t.upscale?.status === "ok" && t.upscale.fresh)
        ? { ok: false, why: "it has no fresh upscale to build on" }
        : upscaleSize(base, m, scale, li, maxScale);
    const out = withThen && m === "latent" && f.thenModel ? thenSize(first, f.thenScale, maxScale) : first;
    return { method: m, first, out };
  };
  const now = real.map((t) => plan(t, f.scale, true));
  const bad = now.filter((p) => !p.out.ok);
  const scaleLabel = (sc: number) => {
    const ps = real.map((t) => plan(t, sc, false));
    const n = ps.filter((p) => !p.first.ok).length;
    const one = real.length === 1 && ps[0].first.ok ? ` → ${ps[0].first.w}×${ps[0].first.h}` : "";
    return { label: `${sc}x${one}${n ? (n === ps.length ? " — not possible" : ` — ${n} can't`) : ""}`, none: n === ps.length && n > 0 };
  };
  const one = real.length === 1 ? now[0] : null;
  const summary = one
    ? (one.out.ok
      ? `${(f.method === "pixel" || f.method === "seedvr2") && f.fromUpscale ? `its upscale ${real[0].upscale?.width}×${real[0].upscale?.height}` : `${real[0].width}×${real[0].height}`} → ${one.first.w}×${one.first.h} ${one.method === "pixel" ? `(${f.pixelModel})` : one.method === "seedvr2" ? `(SeedVR2 ${f.seedvr2Model ?? opts?.seedvr2?.default ?? ""})` : "(re-sample)"}`
        + (one.method === "latent" && f.thenModel ? ` → ${one.out.w}×${one.out.h} (${f.thenModel})` : "")
      : `Can't: ${one.out.why}`)
    : bad.length ? `${bad.length} of ${real.length} can't at these settings (${bad[0].out.why})` : "";

  const submit = () => {
    void upscale(upscaleRequestOf(f, ask, refineModel && f.method !== "pixel"), ask.takes ? ask.takes.map((t) => `${t.shot}|${t.take}`).join(",") : "cut");
    closeUpscale();
  };
  const canQueue = !!ep && !!opts && count > 0 && bad.length < real.length
    && (f.method === "latent" ? latentSome : f.method === "pixel" ? pixelReady && !!f.pixelModel
      : f.method === "seedvr2" ? sv2Ready : latentSome || pixelReady);

  return (
    <Dialog
      title={<>{ask.title} <span className="h3-muted h3-small">{ask.pass} · {f.scale}x{f.method !== "pixel" && f.thenModel ? ` then ${f.thenScale}x` : ""}</span></>}
      onClose={closeUpscale}
      footer={
        <>
          <span className="h3-muted h3-small h3-grow">{count} take{count === 1 ? "" : "s"}{targets.length ? ` · ${targets.join(", ")}` : ""}</span>
          <button className="h3-btn" onClick={closeUpscale}>Cancel</button>
          <button className="h3-btn h3-primary" disabled={!canQueue} onClick={submit}>
            <i className="pi pi-arrow-up-right" /> Queue upscale{count === 1 ? "" : "s"}
          </button>
        </>
      }
    >
      {err && <div className="h3-error h3-small">Couldn't read this ComfyUI's upscale options: {err}</div>}
      {!opts && !err && <div className="h3-muted h3-small"><i className="pi pi-spin pi-spinner" /> Reading what this ComfyUI can do…</div>}
      {opts && (
        <div className="h3-col" style={{ gap: 8 }}>
          <div className="h3-col" style={{ gap: 3 }}>
            <span className="h3-h">Method</span>
            <label className="h3-check" title="A latent re-sample where the take's target has one (H3, LTX-2), the pixel method elsewhere">
              <input type="radio" name="up-method" checked={f.method === "auto"} onChange={() => set({ method: "auto" })} />
              Best for each take{latentAll ? " (re-sample)" : noLatent.length ? ` (pixel for ${noLatent.join(", ")})` : ""}
            </label>
            <label className="h3-check" title="The take re-sampled at 2x from late in its schedule, under its own prompt and references, its audio held: adds real detail">
              <input type="radio" name="up-method" disabled={!latentSome} checked={f.method === "latent"} onChange={() => set({ method: "latent" })} />
              Re-sample (latent){!latentSome ? " — not for these targets here" : !latentAll ? ` — ${noLatent.length ? `not for ${noLatent.join(", ")}` : "not ready for all"}` : ""}
            </label>
            <label className="h3-check" title="An upscale model (RealESRGAN, UltraSharp…) over the frames, the take's audio copied on: fast, any target, adds no generated detail">
              <input type="radio" name="up-method" disabled={!pixelReady} checked={f.method === "pixel"} onChange={() => set({ method: "pixel" })} />
              Pixel (upscale model){!pixelReady ? " — not ready" : ""}
            </label>
            <label className="h3-check" title="SeedVR2: a one-step video restoration model (ComfyUI's own nodes), for any take, no prompt; the sharpest stills, a little more frame-to-frame shimmer than a re-sample (our frequency split after it halves that)">
              <input type="radio" name="up-method" disabled={!sv2Ready} checked={f.method === "seedvr2"} onChange={() => set({ method: "seedvr2" })} />
              SeedVR2 (restore){!sv2Ready ? " — not ready" : ""}
            </label>
            {!sv2Ready && (opts.seedvr2?.missing.length ?? 0) > 0 && (
              <div className="h3-muted h3-small">SeedVR2 needs: {opts.seedvr2!.missing.join("; ")}</div>
            )}
            {!pixelReady && opts.pixel.missing.length > 0 && (
              <div className="h3-muted h3-small">Pixel needs: {opts.pixel.missing.join("; ")}</div>
            )}
          </div>
          {f.method === "seedvr2" && sv2Ready && (
            <label className="h3-col" style={{ gap: 2 }}>
              <span className="h3-h">SeedVR2 model</span>
              <select value={f.seedvr2Model ?? opts.seedvr2!.default} onChange={(e) => set({ seedvr2Model: e.target.value })}>
                {opts.seedvr2!.models.map((m) => <option key={m} value={m}>{m}{m === opts.seedvr2!.default ? " (default)" : ""}</option>)}
              </select>
            </label>
          )}
          {f.method !== "seedvr2" && (f.method !== "latent" || refineModel) && pixelReady && (
            <label className="h3-col" style={{ gap: 2 }}>
              <span className="h3-h">Upscale model{f.method === "pixel" ? "" : refineModel ? " (pixel takes, and Wan's re-sample starts with it)" : " (for takes without a re-sample)"}</span>
              <select value={f.pixelModel ?? ""} onChange={(e) => set({ pixelModel: e.target.value })}>
                {opts.pixel.models.map((m) => <option key={m} value={m}>{m}{m === opts.pixel.default ? " (default)" : ""}</option>)}
              </select>
            </label>
          )}
          {f.method !== "pixel" && f.method !== "seedvr2" && latentSome && (
            <label className="h3-col" style={{ gap: 2 }}>
              <span className="h3-h">Detail (re-sample)</span>
              <select value={f.detail} onChange={(e) => set({ detail: Number(e.target.value) as 0 | 1 | 2 })}>
                {opts.details.map((d) => <option key={d} value={d}>{DETAIL_LABELS[d] ?? `${d} steps earlier`}</option>)}
              </select>
            </label>
          )}
          {(f.method === "pixel" || f.method === "seedvr2") && real.some((t) => t.upscale?.status === "ok" && t.upscale.fresh) && (
            <label className="h3-check" title="Run the upscale model on each take's existing upscale (say, a re-sample done earlier) instead of on the take; the upscale is replaced by the bigger one, and its history kept">
              <input type="checkbox" checked={f.fromUpscale} onChange={(e) => set({ fromUpscale: e.target.checked })} />
              On top of the existing upscale
            </label>
          )}
          <label className="h3-col" style={{ gap: 2 }}>
            <span className="h3-h">Scale</span>
            <select value={f.scale} onChange={(e) => set({ scale: Number(e.target.value) })}>
              {SCALES.map((sc) => {
                const l = scaleLabel(sc);
                return <option key={sc} value={sc} disabled={l.none}>{l.label}</option>;
              })}
            </select>
          </label>
          {f.method !== "pixel" && f.method !== "seedvr2" && latentSome && pixelReady && (
            <div className="h3-col" style={{ gap: 3 }}>
              <label className="h3-check" title="After the re-sample, an upscale model takes the frames further in the same job: e.g. re-sample 2x then RealESRGAN_x2 = 4x, with generated detail in the first half">
                <input type="checkbox" checked={!!f.thenModel} onChange={(e) => set({ thenModel: e.target.checked ? (f.pixelModel ?? opts.pixel.default) : null })} />
                Then an upscale model (re-sampled takes)
              </label>
              {f.thenModel && (
                <div className="h3-row" style={{ gap: 6 }}>
                  <select value={f.thenModel} onChange={(e) => set({ thenModel: e.target.value })}>
                    {opts.pixel.models.map((m) => <option key={m} value={m}>{m}</option>)}
                  </select>
                  <select value={f.thenScale} onChange={(e) => set({ thenScale: Number(e.target.value) })}>
                    {SCALES.map((sc) => <option key={sc} value={sc}>{sc}x more</option>)}
                  </select>
                </div>
              )}
            </div>
          )}
          {summary && <div className={`h3-small ${bad.length ? "h3-error" : "h3-muted"}`}>{summary}</div>}
          <div className="h3-row" style={{ gap: 8, flexWrap: "wrap" }}>
            <label className="h3-col" style={{ gap: 2 }} title="How the upscale's mp4 is written. Auto: H.264 on the GPU (NVENC) up to 4096 wide, x264 on the CPU past that (slower, but it plays in the editor). GPU (NVENC) past 4096 wide writes HEVC: fast, but the browser may show it black">
              <span className="h3-h">Encoder</span>
              <select value={f.encoder} onChange={(e) => set({ encoder: e.target.value as UpscaleForm["encoder"] })}>
                <option value="auto">Auto (GPU when it can)</option>
                <option value="nvenc">GPU (NVENC; HEVC past 4096)</option>
                <option value="x264">CPU (x264)</option>
              </select>
            </label>
            {f.method !== "seedvr2" && (f.method !== "latent" || !!f.thenModel) && (
              <label className="h3-col" style={{ gap: 2 }} title="The upscale model's precision: 16-bit is about twice as fast and looks the same; 32-bit is how ComfyUI's own node runs it">
                <span className="h3-h">Model precision</span>
                <select value={f.precision} onChange={(e) => set({ precision: e.target.value as UpscaleForm["precision"] })}>
                  <option value="fp16">16-bit (fast)</option>
                  <option value="fp32">32-bit</option>
                </select>
              </label>
            )}
          </div>
          {(f.method === "seedvr2" ? sv2Ready : (f.method !== "latent" || !!f.thenModel || refineModel) && pixelReady) && (
            <div className="h3-col" style={{ gap: 3 }}>
              <span className="h3-h">Finish ({f.method === "seedvr2" ? "SeedVR2" : "upscale model"})</span>
              <label className="h3-check" title="Colour and tone from the original frames, only the fine detail from the model: upscale models shift colour a little, and this keeps an upscaled clip matching its neighbours in the cut">
                <input type="checkbox" checked={f.frequencySplit} onChange={(e) => set({ frequencySplit: e.target.checked })} />
                Keep the original's colour
              </label>
              <div className="h3-row" style={{ gap: 8, flexWrap: "wrap" }}>
                <label className="h3-col" style={{ gap: 2 }} title="Fade the detail the model invents where the original was soft (out-of-focus backgrounds, bokeh), so shallow depth of field stays shallow">
                  <span className="h3-small h3-muted">Keep soft areas soft</span>
                  <select value={f.keepSoft} onChange={(e) => set({ keepSoft: Number(e.target.value) })}>
                    <option value={0}>Off</option>
                    <option value={0.5}>Some</option>
                    <option value={1}>Full</option>
                  </select>
                </label>
                <label className="h3-col" style={{ gap: 2 }} title="Film grain after the model (which scrubs grain away), different each frame; not added before a Wan re-sample">
                  <span className="h3-small h3-muted">Grain</span>
                  <select value={f.grain} onChange={(e) => set({ grain: Number(e.target.value) })}>
                    <option value={0}>Off</option>
                    <option value={0.02}>Light</option>
                    <option value={0.04}>Medium</option>
                    <option value={0.06}>Heavy</option>
                  </select>
                </label>
              </div>
            </div>
          )}
          <label className="h3-check" title="Upscale again even where the take already has a fresh upscale (it is replaced)">
            <input type="checkbox" checked={f.redo} onChange={(e) => set({ redo: e.target.checked })} />
            Again, where already upscaled
          </label>
          {f.method !== "pixel" && f.method !== "seedvr2" && latentSome && (
            <label className="h3-check" title="Re-encode the take's video instead of starting from its saved latent (the only way for a take that kept none)">
              <input type="checkbox" checked={f.vae} onChange={(e) => set({ vae: e.target.checked })} />
              From the video, not the saved latent
            </label>
          )}
        </div>
      )}
    </Dialog>
  );
}
