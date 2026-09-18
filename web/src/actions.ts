// Everything that talks to the API and changes the store. Components call these.

import { ApiError, errText } from "./api";
import { api, host } from "./host";
import { absPath, promptText, sameEp, tn } from "./lib/format";
import {
  detailKey, persistPrefs, statusKey, store, type AppState, type CompareMode,
} from "./store";
import type {
  EpisodeStatus, Lora, OverrideFields, Pass, ProgressEvent, PromptEvent, RenderRequest,
  RenderResult, Seed, SeedMode, ShotDetail, TakeEvent, TakeRef,
} from "./types";

const set = store.set;
const get = store.get;

export function report(summary: string, e: unknown) {
  host().toast("error", summary, errText(e));
}

function setBusy(key: string, on: boolean) {
  set((s) => {
    const busy = { ...s.busy };
    if (on) busy[key] = true;
    else delete busy[key];
    return { busy };
  });
}

async function withBusy<T>(key: string, fn: () => Promise<T>): Promise<T> {
  setBusy(key, true);
  try {
    return await fn();
  } finally {
    setBusy(key, false);
  }
}

// ---------------------------------------------------------------------------
// start-up, config, episodes
// ---------------------------------------------------------------------------

let started = false;

/** Load config and episodes, restore the last episode, subscribe to live events. */
export async function start() {
  if (started) return;
  started = true;
  persistPrefs(store);
  wireEvents();
  await loadConfig();
  await loadEpisodes();
}

export async function loadConfig() {
  try {
    const config = await api().getConfig();
    set({ config, configError: null });
  } catch (e) {
    set({ configError: errText(e) });
  }
}

export async function saveRoots(roots: string[]) {
  return withBusy("config", async () => {
    try {
      const config = await api().putConfig(roots);
      set({ config, configError: null });
      await loadEpisodes();
      return true;
    } catch (e) {
      report("Couldn't save the project roots", e);
      return false;
    }
  });
}

export async function loadEpisodes() {
  try {
    const episodes = await api().episodes();
    set({ episodes, episodesError: null });
    const s = get();
    const keep = s.ep && episodes.find((e) => sameEp(e.ep, s.ep));
    const ep = keep ? keep.ep : episodes[0]?.ep ?? null;
    if (ep !== s.ep) selectEpisode(ep);
    else if (ep && !s.status[statusKey(ep, s.pass)]) void refreshEpisode();
  } catch (e) {
    set({ episodesError: errText(e) });
  }
}

export function selectEpisode(ep: string | null) {
  set({ ep, shot: null, take: null, viewer: null, menu: null, redo: null, build: { busy: false, result: null, error: null } });
  if (ep) void refreshEpisode();
}

export function setPass(pass: Pass) {
  if (pass === get().pass) return;
  set({ pass, take: null, menu: null });
  void refreshEpisode();
  const shot = get().shot;
  if (shot) void loadDetail(shot);
}

export function setZoom(zoom: number) {
  set({ zoom });
}

// ---------------------------------------------------------------------------
// episode status and shot detail
// ---------------------------------------------------------------------------

const inflight = new Map<string, Promise<EpisodeStatus | undefined>>();

export function refreshEpisode(ep = get().ep, pass = get().pass): Promise<EpisodeStatus | undefined> {
  if (!ep) return Promise.resolve(undefined);
  const key = statusKey(ep, pass);
  const running = inflight.get(key);
  if (running) return running;
  const p = (async () => {
    set((s) => ({ statusLoading: { ...s.statusLoading, [key]: true } }));
    try {
      const st = await api().episode(ep, pass);
      set((s) => {
        const statusError = { ...s.statusError };
        delete statusError[key];
        return { status: { ...s.status, [key]: st }, statusError };
      });
      void afterStatus(ep, pass, st);
      return st;
    } catch (e) {
      set((s) => ({ statusError: { ...s.statusError, [key]: errText(e) } }));
      return undefined;
    } finally {
      set((s) => {
        const statusLoading = { ...s.statusLoading };
        delete statusLoading[key];
        return { statusLoading };
      });
      inflight.delete(key);
    }
  })();
  inflight.set(key, p);
  return p;
}

/** After a status load: fetch the other pass if a placeholder needs it, and learn
 * the prompt ids of queued takes so websocket progress can be matched to them. */
async function afterStatus(ep: string, pass: Pass, st: EpisodeStatus) {
  const other = st.shots.some((s) => s.cut.placeholder && s.cut.pass !== pass);
  if (other) {
    const op: Pass = pass === "proxy" ? "final" : "proxy";
    if (!get().status[statusKey(ep, op)]) void refreshEpisode(ep, op);
  }
  const known = new Set(Object.values(get().prompts).map((r) => `${r.pass}|${r.shot}|${r.take}`));
  const learned: Record<string, TakeRef> = {};
  const needDetail: string[] = [];
  for (const s of st.shots) {
    for (const t of s.takes) {
      if (t.status !== "queued" || known.has(`${pass}|${s.shot}|${t.take}`)) continue;
      if (t.comfy_prompt_id) learned[t.comfy_prompt_id] = { ep, pass, shot: s.shot, take: t.take };
      else if (!needDetail.includes(s.shot)) needDetail.push(s.shot);
    }
  }
  // TODO(contract): see api.ts; one /h3pipe/shot per shot with a queued take
  for (const shot of needDetail.slice(0, 24)) {
    const d = await loadDetail(shot, pass, true, ep);
    for (const t of d?.takes ?? []) {
      const pid = t.sidecar?.comfy_prompt_id;
      if (t.status === "queued" && pid) learned[pid] = { ep, pass, shot, take: t.take };
    }
  }
  if (Object.keys(learned).length) set((s) => ({ prompts: { ...s.prompts, ...learned } }));
  if (st.shots.some((s) => s.takes.some((t) => t.status === "queued"))) void refreshQueue();
}

/** Which of our prompts ComfyUI is running / has pending (ComfyUI's own /queue). */
export async function refreshQueue() {
  try {
    const q = await api().comfyQueue();
    set({ running: q.running[0] ?? null, pending: q.pending });
  } catch {
    /* informational only */
  }
}

export async function loadDetail(shot: string, pass = get().pass, force = false, ep = get().ep): Promise<ShotDetail | undefined> {
  if (!ep) return undefined;
  const key = detailKey(ep, pass, shot);
  if (!force && get().details[key]) return get().details[key];
  try {
    const d = await api().shot(ep, pass, shot);
    set((s) => {
      const detailError = { ...s.detailError };
      delete detailError[key];
      return { details: { ...s.details, [key]: d }, detailError };
    });
    return d;
  } catch (e) {
    set((s) => ({ detailError: { ...s.detailError, [key]: errText(e) } }));
    return undefined;
  }
}

let refreshTimer: ReturnType<typeof setTimeout> | null = null;

/** Coalesce bursts of events into one refetch of what's on screen. */
export function scheduleRefresh(delay = 200) {
  if (refreshTimer) clearTimeout(refreshTimer);
  refreshTimer = setTimeout(() => {
    refreshTimer = null;
    const s = get();
    if (!s.ep) return;
    const ep = s.ep;
    // every loaded pass of this episode
    for (const key of Object.keys(s.status)) {
      const [e, p] = key.split("|");
      if (e === ep) void refreshEpisode(ep, p as Pass);
    }
    if (!s.status[statusKey(ep, s.pass)]) void refreshEpisode();
    // drop cached details for this episode; refetch the ones on screen
    set((st) => {
      const details: typeof st.details = {};
      for (const [k, v] of Object.entries(st.details)) if (!k.startsWith(ep + "|")) details[k] = v;
      return { details };
    });
    const onScreen = new Set<string>();
    if (s.shot) onScreen.add(`${s.pass}|${s.shot}`);
    if (s.viewer) onScreen.add(`${s.viewer.pass}|${s.viewer.shot}`);
    if (s.redo) onScreen.add(`${s.redo.pass}|${s.redo.shot}`);
    for (const k of onScreen) {
      const [p, shot] = k.split("|");
      void loadDetail(shot, p as Pass, true, ep);
    }
  }, delay);
}

// ---------------------------------------------------------------------------
// selection and floating UI
// ---------------------------------------------------------------------------

export function select(shot: string | null, take: number | null = null) {
  set({ shot, take });
  if (shot) void loadDetail(shot);
}

export function toggleExpanded(shot: string) {
  set((s) => ({ expanded: { ...s.expanded, [shot]: !s.expanded[shot] } }));
}

export function toggleSequence(key: string) {
  set((s) => ({ collapsedSeq: { ...s.collapsedSeq, [key]: !s.collapsedSeq[key] } }));
}

export function openViewer(shot: string, a: number | null, b: number | null = null, mode?: CompareMode, pass = get().pass) {
  const prev = get().viewer;
  set({
    viewer: {
      shot,
      pass,
      a,
      b,
      mode: mode ?? (b != null ? "side" : prev?.mode === "single" || !prev ? "single" : prev.mode),
      target: b != null || mode === "side" || mode === "wipe" ? "b" : "a",
    },
    menu: null,
    shot,
    take: a,
  });
  void loadDetail(shot, pass);
}

export function updateViewer(patch: Partial<NonNullable<AppState["viewer"]>>) {
  const v = get().viewer;
  if (v) set({ viewer: { ...v, ...patch } });
}

export function closeViewer() {
  set({ viewer: null });
}

export function openMenu(x: number, y: number, shot: string, take: number | null, pass = get().pass) {
  set({ menu: { x, y, shot, take, pass } });
}

export function closeMenu() {
  if (get().menu) set({ menu: null });
}

export function openRedo(shot: string, parent: number | null, pass = get().pass) {
  set({ redo: { shot, parent, pass }, menu: null });
  void loadDetail(shot, pass);
  void loadModels();
}

export function closeRedo() {
  set({ redo: null });
}

export function openSidecar(shot: string, take: number, pass = get().pass) {
  set({ sidecar: { shot, take, pass }, menu: null });
  void loadDetail(shot, pass);
}

export function closeSidecar() {
  set({ sidecar: null });
}

let toastId = 0;
export function pushToast(t: Omit<AppState["toasts"][number], "id">, ms = 6000) {
  const id = ++toastId;
  set((s) => ({ toasts: [...s.toasts, { ...t, id }].slice(-5) }));
  setTimeout(() => dismissToast(id), t.severity === "error" ? ms * 2 : ms);
}

export function dismissToast(id: number) {
  set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }));
}

// ---------------------------------------------------------------------------
// the cut
// ---------------------------------------------------------------------------

export async function pickTake(shot: string, take: number | null, fromPass: Pass | null = null) {
  const s = get();
  if (!s.ep) return;
  const ep = s.ep;
  const pass = s.pass;
  const req = { ep, pass, shot, take, from_pass: fromPass && fromPass !== pass ? fromPass : null };
  return withBusy(`pick|${shot}`, async () => {
    try {
      await api().pick(req);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && take != null) {
        if (!confirm(`${errText(e)}\n\nPick ${shot} ${tn(take)} anyway?`)) return;
        try {
          await api().pick({ ...req, force: true });
        } catch (e2) {
          report(`Couldn't pick ${shot} ${tn(take)}`, e2);
          return;
        }
      } else {
        report(`Couldn't pick ${shot} ${take != null ? tn(take) : "latest"}`, e);
        return;
      }
    }
    host().toast("success", take != null ? `${shot}: the ${pass} cut now uses ${tn(take)}` : `${shot}: back to the latest usable take`);
    await refreshEpisode(ep, pass);
  });
}

// ---------------------------------------------------------------------------
// build, render, cancel, assemble
// ---------------------------------------------------------------------------

export async function build() {
  const ep = get().ep;
  if (!ep) return;
  set({ build: { busy: true, result: null, error: null } });
  try {
    const result = await api().build(ep);
    set({ build: { busy: false, result, error: null } });
    if (!result.ok) host().toast("error", "The build failed", firstError(result));
    else host().toast("success", "Built both passes");
    await loadEpisodes();
    scheduleRefresh(0);
  } catch (e) {
    set({ build: { busy: false, result: null, error: errText(e) } });
    report("Build didn't run", e);
  }
}

function firstError(r: { passes: Partial<Record<Pass, { ok: boolean; error: string }>> }): string {
  for (const p of ["final", "proxy"] as Pass[]) {
    const x = r.passes[p];
    if (x && !x.ok) return `${p}: ${x.error}`;
  }
  return "";
}

export function baseRender(ep: string, pass: Pass, shots: string[]): RenderRequest {
  return {
    ep, pass, shots, redo: false, seed_mode: "auto", seed: null, model: null, loras: null,
    steps: null, prompt: null, parent_take: null, note: "",
  };
}

/** Queue takes and remember their prompt ids. Returns the result, or undefined on error. */
export async function queueRender(req: RenderRequest): Promise<RenderResult | undefined> {
  const key = `render|${req.pass}|${req.shots.join(",")}`;
  return withBusy(key, async () => {
    try {
      const r = await api().render(req);
      const learned: Record<string, TakeRef> = {};
      for (const q of r.queued) learned[q.prompt_id] = { ep: req.ep, pass: req.pass, shot: q.shot, take: q.take };
      set((s) => ({ prompts: { ...s.prompts, ...learned } }));
      const bits: string[] = [];
      if (r.queued.length) bits.push(`queued ${r.queued.map((q) => `${q.shot} ${tn(q.take)}`).join(", ")}`);
      if (r.skipped.length) bits.push(`skipped ${r.skipped.map((x) => `${x.shot} (${x.reason})`).join(", ")}`);
      if (r.queued.length) host().toast("success", `Queued ${r.queued.length} take(s)`, bits.join("; "));
      else if (r.skipped.length) host().toast("info", "Nothing queued", bits.join("; "));
      for (const x of r.errors) host().toast("error", `${x.shot} didn't queue`, x.error);
      scheduleRefresh(0);
      return r;
    } catch (e) {
      report("Render request failed", e);
      return undefined;
    }
  });
}

export function renderShots(shots: string[], redo = false) {
  const s = get();
  if (!s.ep || !shots.length) return Promise.resolve(undefined);
  return queueRender({ ...baseRender(s.ep, s.pass, shots), redo });
}

export async function cancelTake(ref: TakeRef) {
  return withBusy(`cancel|${ref.pass}|${ref.shot}|${ref.take}`, async () => {
    try {
      await api().cancel(ref);
      host().toast("info", `Cancelled ${ref.shot} ${tn(ref.take)}`);
      scheduleRefresh(0);
    } catch (e) {
      report(`Couldn't cancel ${ref.shot} ${tn(ref.take)}`, e);
    }
  });
}

export async function assemble(partial = true) {
  const s = get();
  if (!s.ep) return;
  set({ assemble: { busy: true, output: null, report: null, error: null } });
  try {
    const r = await api().assemble(s.ep, s.pass, partial);
    set({ assemble: { busy: false, output: r.output, report: r.report, error: r.ok ? null : r.report } });
    if (r.ok) host().toast("success", "Assembled", absPath(s.ep, r.output));
    else host().toast("error", "Assemble failed", r.report.slice(-400));
  } catch (e) {
    set({ assemble: { busy: false, output: null, report: null, error: errText(e) } });
    report("Assemble failed", e);
  }
}

// ---------------------------------------------------------------------------
// overrides and redo
// ---------------------------------------------------------------------------

export async function saveOverride(shot: string, pass: Pass, both: boolean, fields: OverrideFields): Promise<boolean> {
  const ep = get().ep;
  if (!ep) return false;
  if (!Object.keys(fields).length) return true;
  return withBusy(`override|${shot}`, async () => {
    try {
      await api().putOverride({ ep, pass, shot, both, fields });
      await Promise.all([loadDetail(shot, pass, true), refreshEpisode(ep, pass)]);
      if (both) {
        const other: Pass = pass === "proxy" ? "final" : "proxy";
        void loadDetail(shot, other, true);
      }
      return true;
    } catch (e) {
      report(`Couldn't save ${shot}'s override`, e);
      return false;
    }
  });
}

export async function revertOverride(shot: string, pass: Pass | null) {
  const ep = get().ep;
  if (!ep) return;
  return withBusy(`override|${shot}`, async () => {
    try {
      await api().deleteOverride(ep, shot, pass ?? undefined);
      host().toast("info", pass ? `${shot}: ${pass} override removed` : `${shot}: override removed`);
      await Promise.all([loadDetail(shot, get().pass, true), refreshEpisode(ep, get().pass)]);
    } catch (e) {
      report(`Couldn't remove ${shot}'s override`, e);
    }
  });
}

export type RedoSeed = { mode: "new" } | { mode: "same"; seed: Seed } | { mode: "typed"; seed: Seed };

export interface RedoPlan {
  shot: string;
  pass: Pass;
  parent: number | null;
  seed: RedoSeed;
  model: string;
  loras: Lora[] | null;
  steps: number;
  prompt: string;
  note: string;
  saveAsOverride: boolean;
}

/** What to send for a redo: the override to write first (if any) and the render. */
export function planRedo(p: RedoPlan, ep: string, d: ShotDetail): { override: OverrideFields | null; render: RenderRequest } {
  const seedMode: SeedMode = p.seed.mode === "new" ? "new" : "auto";
  const seed: Seed | null = p.seed.mode === "new" ? null : p.seed.seed;
  const base = { ...baseRender(ep, p.pass, [p.shot]), redo: true, parent_take: p.parent, note: p.note, seed_mode: seedMode, seed };
  if (!p.saveAsOverride) {
    return {
      override: null,
      render: { ...base, model: p.model || null, loras: p.loras, steps: p.steps, prompt: p.prompt },
    };
  }
  // The override then carries the settings, so the take records them as overrides.
  // Only changed fields are written: re-sending an unchanged prompt would re-stamp
  // its base_hash and silently clear an "override stale" warning.
  const f: OverrideFields = {};
  const eff = d.effective;
  if (p.prompt !== eff.prompt) f.prompt = p.prompt === d.built_prompt ? null : p.prompt;
  if (p.model !== eff.model) f.model = p.model || null;
  if (!sameLoras(p.loras, eff.loras)) f.loras = p.loras;
  if (p.steps !== eff.steps) f.steps = p.steps;
  if (seed != null && seed !== d.override.seed) f.seed = seed;
  return { override: Object.keys(f).length ? f : null, render: base };
}

export function sameLoras(a: Lora[] | null | undefined, b: Lora[] | null | undefined): boolean {
  if (a == null || b == null) return a == b;
  return a.length === b.length && a.every((l, i) => l.name === b[i].name && l.strength === b[i].strength);
}

export async function runRedo(p: RedoPlan): Promise<boolean> {
  const ep = get().ep;
  if (!ep) return false;
  const d = get().details[detailKey(ep, p.pass, p.shot)] ?? (await loadDetail(p.shot, p.pass, false, ep));
  if (!d) {
    host().toast("error", `Couldn't load ${p.shot}`, get().detailError[detailKey(ep, p.pass, p.shot)]);
    return false;
  }
  const plan = planRedo(p, ep, d);
  if (plan.override) {
    const ok = await saveOverride(p.shot, p.pass, false, plan.override);
    if (!ok) return false;
  }
  const r = await queueRender(plan.render);
  return !!r && r.queued.length > 0;
}

/** The prompt a take was rendered with, from its frozen shotlist. */
export async function takePrompt(shot: string, take: number, pass = get().pass): Promise<string | null> {
  const ep = get().ep;
  if (!ep) return null;
  const d = await loadDetail(shot, pass);
  const file = d?.takes.find((t) => t.take === take)?.files.shotlist;
  if (!file) return null;
  const doc = await api().readJson<{ shots?: { prompt?: string | string[] }[] }>(ep, file);
  const p = doc.shots?.[0]?.prompt;
  return p == null ? null : promptText(p);
}

// ---------------------------------------------------------------------------
// models
// ---------------------------------------------------------------------------

let modelsLoading: Promise<void> | null = null;

export function loadModels(force = false): Promise<void> {
  if (!force && (get().models || modelsLoading)) return modelsLoading ?? Promise.resolve();
  modelsLoading = (async () => {
    try {
      const [models, loras] = await Promise.all([api().models(), api().loras()]);
      set({ models, loras, modelsError: null });
    } catch (e) {
      set({ modelsError: errText(e) });
    } finally {
      modelsLoading = null;
    }
  })();
  return modelsLoading;
}

// ---------------------------------------------------------------------------
// misc
// ---------------------------------------------------------------------------

export async function copyText(text: string, what = "Path") {
  try {
    await navigator.clipboard.writeText(text);
    host().toast("info", `${what} copied`, text);
  } catch {
    // clipboard blocked (no focus, insecure context): show it so it can be copied by hand
    prompt(`${what}:`, text);
  }
}

// ---------------------------------------------------------------------------
// live events
// ---------------------------------------------------------------------------

function num(v: unknown): number {
  return typeof v === "number" && Number.isFinite(v) ? v : 0;
}

function isOurs(pid: string | undefined): pid is string {
  return !!pid && !!get().prompts[pid];
}

export function wireEvents() {
  const h = host();
  h.on("progress", (d) => {
    const p = (d ?? {}) as ProgressEvent;
    if (!p.prompt_id) return;
    const pid = p.prompt_id;
    set((s) => ({ running: pid, progress: { ...s.progress, [pid]: { value: num(p.value), max: num(p.max) } } }));
  });
  h.on("execution_start", (d) => {
    const pid = (d as PromptEvent | null)?.prompt_id;
    if (pid) set((s) => ({ running: pid, pending: s.pending.filter((x) => x !== pid) }));
  });
  h.on("executing", (d) => {
    // ComfyUI sends the node id; null means the prompt finished
    if (d == null) set({ running: null });
  });
  const finished = (d: unknown) => {
    const pid = (d as PromptEvent | null)?.prompt_id;
    if (!pid) return;
    set((s) => {
      const progress = { ...s.progress };
      delete progress[pid];
      return { progress, running: s.running === pid ? null : s.running };
    });
    if (isOurs(pid)) scheduleRefresh();
  };
  h.on("execution_success", finished);
  h.on("execution_interrupted", finished);
  h.on("execution_error", (d) => {
    const e = (d ?? {}) as PromptEvent;
    const pid = e.prompt_id;
    if (isOurs(pid)) {
      const r = get().prompts[pid];
      host().toast("error", `${r.shot} ${tn(r.take)} failed in ComfyUI`, [e.node_type, e.exception_message].filter(Boolean).join(": "));
    }
    finished(d);
  });
  h.on("h3pipe.take", (d) => {
    const t = (d ?? {}) as TakeEvent;
    if (!sameEp(t.ep, get().ep)) return;
    if (t.status === "ok" && isTracked(t)) host().toast("success", `${t.shot} ${tn(t.take)} rendered`);
    scheduleRefresh();
  });
  h.on("h3pipe.episode", (d) => {
    const ep = (d as { ep?: string } | null)?.ep;
    if (!sameEp(ep, get().ep)) return;
    scheduleRefresh();
  });
}

function isTracked(t: TakeEvent): boolean {
  return Object.values(get().prompts).some((r) => r.pass === t.pass && r.shot === t.shot && r.take === t.take);
}
