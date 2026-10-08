// The Post-process dialog (h3post, docs/POST_PROCESSING.md), opened from a
// take's menu (one take) or the cut menu (that pass's whole cut). A post is a
// take's fresh upscale finished: an enhance step (the tiers: draft, a pixel
// model at 1x; production, SeedVR2 7B; cinematic, SUPIR) then motion blur.
// What it offers comes from GET /h3pipe/post/options: the tiers, whether this
// ComfyUI can run each, and the episode's post.master recipe.

import { useEffect, useState } from "react";
import { errText } from "../api";
import { api } from "../host";
import { closePost, queuePost } from "../actions";
import { useApp } from "../store";
import type { PostOptions, PostRequest } from "../types";
import { Dialog } from "./Dialogs";

const TIER_LABEL: Record<string, string> = {
  draft: "Draft: an upscale model at 1x (a sharpen, seconds a shot)",
  production: "Production: SeedVR2 7B clean-up (~4.5 min a 5 s shot at 1080p)",
  cinematic: "Cinematic: SUPIR at a low denoise (slow: ~30 min a 5 s shot)",
};

export function PostDialog() {
  const ask = useApp((s) => s.postAsk);
  if (!ask) return null;
  return <PostBody key={`${ask.title}|${(ask.takes ?? []).map((t) => `${t.shot}:${t.take}`).join(",")}`} />;
}

function PostBody() {
  const ask = useApp((s) => s.postAsk)!;
  const ep = useApp((s) => s.ep);
  const [opts, setOpts] = useState<PostOptions | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [useRecipe, setUseRecipe] = useState(true);
  const [tier, setTier] = useState("production");
  const [blur, setBlur] = useState(0.3);
  const [redo, setRedo] = useState(!!ask.redo);

  useEffect(() => {
    if (!ep) return;
    api().postOptions(ep)
      .then((o) => { setOpts(o); setErr(null); if (!o.recipe) setUseRecipe(false); setBlur(o.blur.default); })
      .catch((e: unknown) => setErr(errText(e)));
  }, [ep]);

  const recipe = opts?.recipe ?? null;
  const byRecipe = useRecipe && !!recipe;
  const chosen = opts?.tiers.find((t) => t.id === tier) ?? null;
  // what this ComfyUI is missing for the choice (the recipe's needs are checked when queued)
  const missing = byRecipe || !opts ? []
    : [...(tier !== "none" ? chosen?.readiness.missing ?? [] : []),
       ...(blur > 0 ? opts.blur.readiness.missing : [])];
  const nothing = !byRecipe && tier === "none" && blur <= 0;
  const count = ask.takes ? ask.takes.length : null;
  const canQueue = !!ep && !!opts && !nothing && missing.length === 0;

  const submit = () => {
    const key = ask.takes ? ask.takes.map((t) => `${t.shot}|${t.take}`).join(",") : "cut";
    const who = ask.takes ? { pass: ask.pass, takes: ask.takes } : { pass: ask.pass, shots: null };
    const req: Omit<PostRequest, "ep"> = byRecipe
      ? { ...who, recipe: true, ...(redo ? { redo: true } : {}) }
      : { ...who, recipe: false, enhance: tier, blur, ...(redo ? { redo: true } : {}) };
    void queuePost(req, key);
    closePost();
  };

  return (
    <Dialog
      title={<>{ask.title} <span className="h3-muted h3-small">{ask.pass} · {byRecipe ? "series recipe" : "this run"}</span></>}
      onClose={closePost}
      footer={
        <>
          <span className="h3-muted h3-small h3-grow">
            {count != null ? `${count} take${count === 1 ? "" : "s"}` : "every upscaled take of the cut"} · each needs a fresh upscale
          </span>
          <button className="h3-btn" onClick={closePost}>Cancel</button>
          <button className="h3-btn h3-primary" disabled={!canQueue} onClick={submit}>
            <i className="pi pi-sparkles" /> Queue post{count === 1 ? "" : "s"}
          </button>
        </>
      }
    >
      {err && <div className="h3-error h3-small">Couldn't read this ComfyUI's post options: {err}</div>}
      {!opts && !err && <div className="h3-muted h3-small"><i className="pi pi-spin pi-spinner" /> Reading what this ComfyUI can do…</div>}
      {opts && (
        <div className="h3-col" style={{ gap: 8 }}>
          {recipe && (
            <div className="h3-col" style={{ gap: 3 }}>
              <label className="h3-check" title="Each take as the series config's post.master says, its shot's own (overrides.json's post) over it">
                <input type="radio" name="post-recipe" checked={byRecipe} onChange={() => setUseRecipe(true)} />
                Series recipe: {recipe.text || "nothing (enhance none, no blur)"}
              </label>
              {byRecipe && Object.entries(recipe.shots).map(([shot, v]) => (
                <div key={shot} className="h3-small h3-muted" style={{ marginLeft: 22 }}>{shot}, its own: {v.text || "none"}</div>
              ))}
              {byRecipe && recipe.problems.map((p) => <div key={p} className="h3-small h3-error">{p}</div>)}
              <label className="h3-check" title="Choose the enhance step and the blur for this run only; the series recipe is left as it is">
                <input type="radio" name="post-recipe" checked={!byRecipe} onChange={() => setUseRecipe(false)} />
                Choose for this run
              </label>
            </div>
          )}
          {!recipe && (
            <div className="h3-small h3-muted">
              The series config has no <code>post.master</code> recipe, so choose for this run
              (or add <code>"post": {"{"}"master": {"{"}"enhance": "production", "motion_blur": 0.3{"}}"}</code>).
            </div>
          )}
          {!byRecipe && (
            <div className="h3-col" style={{ gap: 6 }}>
              <label className="h3-col" style={{ gap: 2 }}>
                <span className="h3-small h3-muted">Enhance</span>
                <select value={tier} onChange={(e) => setTier(e.target.value)}>
                  {opts.tiers.map((t) => (
                    <option key={t.id} value={t.id}>
                      {(TIER_LABEL[t.id] ?? t.id) + (t.readiness.status === "not_ready" ? " (not ready here)" : "")}
                    </option>
                  ))}
                  <option value="none">None: motion blur only</option>
                </select>
              </label>
              <label className="h3-col" style={{ gap: 2 }}
                     title="Shutter blur along each pixel's motion (optical flow), as a fraction of the frame interval: 0.3 smooths blocky motion, 0.5 is a 180° film shutter. Still areas are left alone">
                <span className="h3-small h3-muted">Motion blur: {blur.toFixed(2)}{blur === 0 ? " (none)" : blur >= 0.5 ? " (180° shutter or more)" : ""}</span>
                <input type="range" min={opts.blur.min} max={Math.min(opts.blur.max, 0.6)} step={0.05} value={blur}
                       onChange={(e) => setBlur(Number(e.target.value))} />
              </label>
              {nothing && <div className="h3-small h3-error">Nothing to do: pick an enhance step or some blur.</div>}
              {missing.map((m) => <div key={m} className="h3-small h3-error">Not ready here: {m}</div>)}
            </div>
          )}
          <label className="h3-check" title="Post again even where the take already has a fresh post with these settings (it is replaced)">
            <input type="checkbox" checked={redo} onChange={(e) => setRedo(e.target.checked)} />
            Again, where already post-processed
          </label>
          <div className="h3-small h3-muted">
            A post is saved beside the upscale (<code>.post.mp4</code>), the same size and sound. Master uses it when
            its <b>Post-process</b> box is ticked.
          </div>
        </div>
      )}
    </Dialog>
  );
}
