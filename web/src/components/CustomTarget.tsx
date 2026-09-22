// A show's own targets (Phase 12c): turn one of ComfyUI's saved workflows into
// a target, confirm what h3pipe worked out and what it guessed, save it as a
// draft, probe-render one shot on it, then enable it for shots.

import { useEffect, useMemo, useState } from "react";
import {
  deleteCustomTarget, enableCustomTarget, inspectTarget, loadWorkflows, probeTarget,
  saveCustomTarget,
} from "../actions";
import { customTargets } from "../lib/targets";
import { useApp } from "../store";
import type { Target, TargetList, TargetProposal, WorkflowFile } from "../types";

/** The fields no graph states, so a person has to (the rest is detected). */
interface Answers {
  id: string;
  label: string;
  short: string;
  fps: string;
  step: string;
  base: string;
  max: string;
  sizeMultiple: string;
  audio: "generate" | "none";
  prompt: "prose" | "prose_silent";
  /** param -> "class_type.field" of the candidate picked for an ambiguity */
  chosen: Record<string, string>;
}

function answersFrom(p: TargetProposal): Answers {
  const spec = p.proposal as Record<string, any>;
  const frames = spec.template?.frames ?? {};
  return {
    id: String(spec.id ?? ""),
    label: String(spec.label ?? ""),
    short: String(spec.short ?? ""),
    fps: String(spec.template?.fps ?? "series"),
    step: String(frames.step ?? 4),
    base: String(frames.base ?? 1),
    max: String(frames.max ?? 121),
    sizeMultiple: String(spec.template?.size_multiple ?? 16),
    audio: spec.capabilities?.audio === "none" ? "none" : "generate",
    prompt: spec.recipe?.prompt === "prose_silent" ? "prose_silent" : "prose",
    chosen: {},
  };
}

/** The proposal with the author's answers in it, ready to save. */
function applyAnswers(p: TargetProposal, a: Answers): Record<string, unknown> {
  const spec = JSON.parse(JSON.stringify(p.proposal)) as Record<string, any>;
  spec.id = a.id.trim();
  spec.label = a.label.trim() || spec.id;
  spec.short = a.short.trim() || spec.label;
  spec.template = {
    ...spec.template,
    fps: a.fps === "series" ? "series" : Number(a.fps) || 24,
    size_multiple: Number(a.sizeMultiple) || 16,
    frames: { step: Number(a.step) || 1, base: Number(a.base) || 1, max: Number(a.max) || 121 },
  };
  spec.capabilities = { ...spec.capabilities, audio: a.audio };
  spec.recipe = { ...spec.recipe, prompt: a.prompt };
  if (a.audio === "none") {
    spec.recipe.policies = ["silent"];
    spec.recipe.policy_fallback = { to: "silent", why: "this model makes no sound" };
  } else {
    spec.recipe.policies = ["generate"];
    delete spec.recipe.policy_fallback;
  }
  for (const amb of p.ambiguous) {
    const picked = a.chosen[amb.param];
    const cand = amb.candidates.find((c) => `${c.class_type}.${c.field}` === picked);
    if (!cand) continue;
    spec.binding.params[amb.param] = {
      class_type: cand.class_type, field: cand.field,
      ...(cand.title ? { title: cand.title } : {}),
    };
  }
  spec.draft = true;
  return spec;
}

/** "Add a target from a workflow", and the show's own targets as they stand. */
export function CustomTargets({ list }: { list: TargetList | null }) {
  const ep = useApp((s) => s.ep);
  const [open, setOpen] = useState(false);
  const mine = useMemo(() => customTargets(list), [list]);
  if (!ep) return null;
  return (
    <div className="h3-col" style={{ gap: 6 }}>
      <div className="h3-h">
        This show's own targets
        <span className="h3-muted h3-small" style={{ textTransform: "none", fontWeight: 400 }}>
          {" "}— a ComfyUI workflow of yours, driven like any other model
        </span>
      </div>
      {mine.map((t) => <MineRow key={t.id} t={t} />)}
      {!mine.length && (
        <div className="h3-small h3-muted">
          None yet. Save a workflow in ComfyUI, then add it here: h3pipe reads the graph and works
          out which widget takes the prompt, the size, the length, the seed and the model.
        </div>
      )}
      {open
        ? <Wizard onClose={() => setOpen(false)} />
        : <div><button className="h3-btn" onClick={() => setOpen(true)}>
            <i className="pi pi-plus" /> Add a target from a workflow…
          </button></div>}
    </div>
  );
}

function MineRow({ t }: { t: Target }) {
  const busy = useApp((s) => !!s.busy["target-save"] || !!s.busy["target-probe"]);
  return (
    <div className="h3-mfile">
      <div className="h3-row">
        <span className={`h3-tier h3-tier-${t.draft ? "accelerator" : "node"}`}>
          {t.draft ? "draft" : "ready"}
        </span>
        <b className="h3-ell h3-grow" title={t.id}>{t.label || t.id}</b>
        <span className="h3-mono h3-small h3-muted h3-ell" style={{ maxWidth: 220 }}
              title={t.graph?.name}>{t.graph?.name}</span>
      </div>
      {t.draft && (
        <div className="h3-small">
          Nothing renders on it yet. Probe one shot, look at the take, then enable it — a wrong
          frame grid or a missing file shows up here rather than mid-episode.
        </div>
      )}
      <div className="h3-row h3-small">
        {t.draft && (
          <>
            <button className="h3-btn" disabled={busy} onClick={() => void probeTarget(t.id)}>
              <i className="pi pi-play" /> Probe render
            </button>
            <button className="h3-btn" disabled={busy} onClick={() => void enableCustomTarget(t.id)}>
              <i className="pi pi-check" /> Enable for shots
            </button>
          </>
        )}
        {!t.draft && (
          <button className="h3-btn" disabled={busy} onClick={() => void enableCustomTarget(t.id, true)}>
            <i className="pi pi-undo" /> Back to draft
          </button>
        )}
        <button className="h3-btn" disabled={busy} title={`Remove ${t.id} from this show`}
                onClick={() => { if (confirm(`Remove ${t.label || t.id} from this show? Takes already rendered on it are kept.`)) void deleteCustomTarget(t.id); }}>
          <i className="pi pi-trash" /> Remove
        </button>
      </div>
    </div>
  );
}

function Wizard({ onClose }: { onClose: () => void }) {
  const busy = useApp((s) => !!s.busy["target-inspect"]);
  const saving = useApp((s) => !!s.busy["target-save"]);
  const [files, setFiles] = useState<WorkflowFile[] | null>(null);
  const [pick, setPick] = useState("");
  const [proposal, setProposal] = useState<TargetProposal | null>(null);
  const [a, setA] = useState<Answers | null>(null);

  useEffect(() => {
    void loadWorkflows().then(setFiles);
  }, []);

  const set = (patch: Partial<Answers>) => setA((prev) => (prev ? { ...prev, ...patch } : prev));
  const read = async () => {
    const r = await inspectTarget({ workflow: pick });
    setProposal(r);
    setA(r ? answersFrom(r) : null);
  };
  const save = async () => {
    if (!proposal || !a) return;
    if (await saveCustomTarget(applyAnswers(proposal, a))) onClose();
  };
  const unanswered = proposal?.ambiguous.filter((x) => !a?.chosen[x.param]) ?? [];

  return (
    <div className="h3-mfile h3-col" style={{ gap: 6 }}>
      <div className="h3-row">
        <b className="h3-grow">Add a target from a workflow</b>
        <button className="h3-btn h3-icon" title="Close" onClick={onClose}>
          <i className="pi pi-times" />
        </button>
      </div>
      <div className="h3-row h3-small">
        <span className="h3-muted">Workflow</span>
        <select className="h3-in h3-grow" value={pick} onChange={(e) => setPick(e.target.value)}>
          <option value="">(choose one of ComfyUI's saved workflows)</option>
          {(files ?? []).map((f) => (
            <option key={f.name} value={f.name}>{f.name}{f.target ? "  (h3pipe's own)" : ""}</option>
          ))}
        </select>
        <button className="h3-btn" disabled={!pick || busy} onClick={() => void read()}>
          <i className={busy ? "pi pi-spin pi-spinner" : "pi pi-search"} /> Read it
        </button>
      </div>
      {!files && <div className="h3-small h3-muted">Asking ComfyUI what it has saved…</div>}
      {proposal && a && (
        <>
          {!!proposal.problems.length && (
            <div className="h3-note h3-note-err h3-small">
              This graph can't be a target yet:
              <ul className="h3-missing-list">{proposal.problems.map((p) => <li key={p}>{p}</li>)}</ul>
            </div>
          )}
          <div className="h3-small">
            Detected: {Object.entries(proposal.matched).map(([k, v]) => `${k} → ${v}`).join(", ") || "nothing"}
          </div>
          <div className="h3-lora">
            <label className="h3-small h3-muted">id</label>
            <input className="h3-in" value={a.id} onChange={(e) => set({ id: e.target.value })} />
            <label className="h3-small h3-muted">name</label>
            <input className="h3-in" value={a.label} onChange={(e) => set({ label: e.target.value })} />
          </div>
          <div className="h3-row h3-small">
            <span className="h3-muted" title="Frames are step × n + base, up to max">frames</span>
            <input className="h3-in" style={{ width: 70 }} value={a.step} onChange={(e) => set({ step: e.target.value })} title="step" />
            <input className="h3-in" style={{ width: 70 }} value={a.base} onChange={(e) => set({ base: e.target.value })} title="base" />
            <input className="h3-in" style={{ width: 90 }} value={a.max} onChange={(e) => set({ max: e.target.value })} title="longest" />
            <span className="h3-muted">size ×</span>
            <input className="h3-in" style={{ width: 70 }} value={a.sizeMultiple} onChange={(e) => set({ sizeMultiple: e.target.value })} />
            <span className="h3-muted">fps</span>
            <input className="h3-in" style={{ width: 80 }} value={a.fps} onChange={(e) => set({ fps: e.target.value })} title={`A number, or "series" to follow the series config`} />
          </div>
          <div className="h3-row h3-small">
            <span className="h3-muted">sound</span>
            <select className="h3-in" value={a.audio} onChange={(e) => set({ audio: e.target.value as Answers["audio"] })}>
              <option value="generate">the model makes it</option>
              <option value="none">silent (lay it in at the edit)</option>
            </select>
            <span className="h3-muted">prompt</span>
            <select className="h3-in" value={a.prompt} onChange={(e) => set({ prompt: e.target.value as Answers["prompt"] })}>
              <option value="prose">prose, with sound and spoken lines</option>
              <option value="prose_silent">prose, dialogue as silent acting</option>
            </select>
          </div>
          {proposal.ambiguous.map((amb) => (
            <div key={amb.param} className="h3-note h3-note-info h3-small">
              <div>{amb.ask}</div>
              <select className="h3-in" value={a.chosen[amb.param] ?? ""}
                      onChange={(e) => set({ chosen: { ...a.chosen, [amb.param]: e.target.value } })}>
                <option value="">(leave {amb.param} out)</option>
                {amb.candidates.map((c) => (
                  <option key={c.node} value={`${c.class_type}.${c.field}`}>
                    {c.class_type}.{c.field}{c.title ? ` — ${c.title}` : ""}
                    {c.value !== undefined ? ` (now ${String(c.value)})` : ""}
                  </option>
                ))}
              </select>
            </div>
          ))}
          {!!proposal.models.length && (
            <div className="h3-small">
              Model files: {proposal.models.map((m) => `${m.param} ${m.file}${m.family ? "" : " (family unknown)"}`).join(", ")}
            </div>
          )}
          {!!proposal.warnings.length && (
            <details className="h3-small">
              <summary>{proposal.warnings.length} thing(s) h3pipe guessed or couldn't bind</summary>
              <ul className="h3-missing-list">{proposal.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
            </details>
          )}
          <div className="h3-row h3-small">
            <button className="h3-btn" disabled={saving || !a.id || !!proposal.problems.length}
                    onClick={() => void save()}>
              <i className={saving ? "pi pi-spin pi-spinner" : "pi pi-save"} /> Save as a draft
            </button>
            {!!unanswered.length && (
              <span className="h3-muted">
                {unanswered.map((x) => x.param).join(", ")} stays unbound until you choose.
              </span>
            )}
          </div>
        </>
      )}
    </div>
  );
}
