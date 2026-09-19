// Everything that talks to the API and changes the store. Components call these.

import { ApiError, errText } from "./api";
import { api, host } from "./host";
import { reachable, sameDir } from "./lib/browse";
import { absPath, promptText, sameEp, tn } from "./lib/format";
import { normPath, splitByMissingRefs } from "./lib/missingRefs";
import { buildPlaylist, startOf, totalDuration, type PlayItem } from "./lib/playlist";
import { keyframeRefId, keyframeSource, type KeyframeEnd } from "./lib/keyframes";
import { hasViews, missingPlan, viewLabel, type RefFilter } from "./lib/refs";
import { renderWarnings } from "./lib/targets";
import {
  detailKey, persistPrefs, statusKey, store, type AppState, type BrowseState, type CompareMode,
  type RefTakeRef,
} from "./store";
import type {
  EpisodeStatus, Lora, OverrideFields, Pass, ProgressEvent, PromptEvent, Ref, RefEvent,
  RefGenerateRequest, RefTake, RenderRequest, RenderResult, Seed, SeedMode, ShotDetail, TakeEvent, TakeRef,
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
  void loadTargets();
  await loadEpisodes();
}

// ---------------------------------------------------------------------------
// targets (Phase 7/8)
// ---------------------------------------------------------------------------

let targetsLoading: Promise<void> | null = null;

/** GET /h3pipe/targets once. A server without it leaves `targets` null, and the
 * UI keeps its pre-target behaviour (no picker, unfiltered model lists). */
export function loadTargets(force = false): Promise<void> {
  if (!force && (get().targets || targetsLoading)) return targetsLoading ?? Promise.resolve();
  targetsLoading = (async () => {
    try {
      const targets = await api().targets();
      set({ targets, targetsError: null });
    } catch (e) {
      set({ targetsError: errText(e) });
    } finally {
      targetsLoading = null;
    }
  })();
  return targetsLoading;
}

const choicesLoading = new Map<string, Promise<void>>();

/** ComfyUI's choices for a combo widget a target binds (cached; failures are
 * quiet: the picker falls back to the generic list). */
export function loadWidgetChoices(classType: string, field: string): Promise<void> {
  const key = `${classType}|${field}`;
  if (get().widgetChoices[key]) return Promise.resolve();
  const running = choicesLoading.get(key);
  if (running) return running;
  const p = (async () => {
    try {
      const c = await api().widgetChoices(classType, field);
      if (c) set((s) => ({ widgetChoices: { ...s.widgetChoices, [key]: c } }));
    } catch {
      /* fall back to the generic list */
    } finally {
      choicesLoading.delete(key);
    }
  })();
  choicesLoading.set(key, p);
  return p;
}

const modelFilesLoading = new Map<string, Promise<void>>();

/** GET /h3pipe/models for one target's model param (cached per target and
 * param; failures are quiet: an older server has no such route, and the
 * picker keeps its flat list). */
export function loadModelFiles(target: string, param = "model", force = false): Promise<void> {
  const key = `${target}|${param}`;
  if (!force && get().modelFiles[key]) return Promise.resolve();
  const running = modelFilesLoading.get(key);
  if (running) return running;
  const p = (async () => {
    try {
      const list = await api().modelFiles(target, param, get().ep);
      set((s) => ({ modelFiles: { ...s.modelFiles, [key]: list } }));
    } catch {
      /* no route: the flat list */
    } finally {
      modelFilesLoading.delete(key);
    }
  })();
  modelFilesLoading.set(key, p);
  return p;
}

/**
 * Retarget a shot: the override's `target`, shared by both passes. `null` (or
 * the built target) goes back to the target the build compiled it for.
 */
export async function setShotTarget(shot: string, target: string | null, builtTarget?: string | null): Promise<boolean> {
  const ep = get().ep;
  if (!ep) return false;
  const value = target && target !== builtTarget ? target : null;
  return withBusy(`override|${shot}`, async () => {
    try {
      await api().putOverride({ ep, pass: get().pass, shot, both: true, fields: { target: value } });
      // shared by both passes: refetch both
      await Promise.all((["final", "proxy"] as Pass[]).flatMap((p) => [
        loadDetail(shot, p, true, ep),
        get().status[statusKey(ep, p)] ? refreshEpisode(ep, p) : Promise.resolve(undefined),
      ]));
      const label = (id: string | null | undefined) => get().targets?.targets.find((t) => t.id === id)?.label ?? id ?? "";
      host().toast(
        "success",
        value ? `${shot} now renders on ${label(value)}` : `${shot} is back on ${label(builtTarget) || "its built target"}`,
        value ? "Both passes. Its prompt, model, LoRAs and steps come from the new target; a per-pass prompt override is ignored." : undefined,
      );
      return true;
    } catch (e) {
      report(`Couldn't change ${shot}'s target`, e);
      return false;
    }
  });
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
  set((s) => ({
    ep, shot: null, take: null, viewer: null, menu: null, redo: null, renderAsk: null, refSel: null,
    cutPlay: { ...s.cutPlay, playing: false, pos: 0 },
    build: { busy: false, result: null, error: null },
  }));
  if (ep) {
    void refreshEpisode();
    if (get().refs[ep] || refsWanted) void loadRefs(ep);
  }
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
  // servers from before comfy_prompt_id: one /h3pipe/shot per shot with a queued take
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
      kind: "takes",
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
  set((s) => ({ viewer: null, cutPlay: { ...s.cutPlay, playing: false } }));
}

export function openMenu(x: number, y: number, shot: string, take: number | null, pass = get().pass,
  frame: number | "last" | null = null) {
  set({ menu: { x, y, shot, take, pass, ...(frame != null ? { frame } : {}) } });
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

export function baseRender(ep: string, pass: Pass, shots: string[], allowMissingRefs = false, target: string | null = null, allowModelMismatch = false): RenderRequest {
  const r: RenderRequest = {
    ep, pass, shots, redo: false, seed_mode: "auto", seed: null, model: null, loras: null,
    steps: null, prompt: null, parent_take: null, note: "",
  };
  // only sent when set: false is the server's default, and older servers don't know it
  if (allowMissingRefs) r.allow_missing_refs = true;
  // the same for a one-off target (Phase 8)
  if (target) r.target = target;
  // and for "render anyway (model mismatch)"
  if (allowModelMismatch) r.allow_model_mismatch = true;
  return r;
}

/** The toasts after a render call: what queued, what was skipped and why, what failed. */
export function renderReport(r: RenderResult): { severity: "success" | "info" | "warn"; summary: string; detail: string }[] {
  const out: { severity: "success" | "info" | "warn"; summary: string; detail: string }[] = [];
  if (r.queued.length) {
    out.push({ severity: "success", summary: `Queued ${r.queued.length} take${r.queued.length > 1 ? "s" : ""}`, detail: r.queued.map((q) => `${q.shot} ${tn(q.take)}`).join(", ") });
  }
  const skipped = r.skipped ?? [];
  const missing = skipped.filter((x) => x.missing_refs?.length);
  const mismatch = skipped.filter((x) => !x.missing_refs?.length && x.model_mismatch?.length);
  const other = skipped.filter((x) => !x.missing_refs?.length && !x.model_mismatch?.length);
  if (mismatch.length) {
    out.push({
      severity: "warn",
      summary: `Skipped ${mismatch.length} shot${mismatch.length > 1 ? "s" : ""}: model mismatch`,
      detail: mismatch.map((x) => `${x.shot}: ${x.model_mismatch!.map((m) => m.message).join("; ")}`).join("\n") +
        "\nPick a model of the right family, or Redo… with “Render anyway (model mismatch)”.",
    });
  }
  if (missing.length) {
    out.push({
      severity: "warn",
      summary: `Skipped ${missing.length} shot${missing.length > 1 ? "s" : ""}: missing refs`,
      detail: missing.map((x) => `${x.shot}: ${x.missing_refs!.map((m) => `${m.slot} ${m.path}`).join(", ")}`).join("\n") +
        "\nMake the refs in the Refs tab, or render anyway (missing pictures become flat grey).",
    });
  }
  if (other.length) {
    out.push({
      severity: r.queued.length ? "info" : "warn",
      summary: r.queued.length ? `Skipped ${other.length} shot${other.length > 1 ? "s" : ""}` : "Nothing queued",
      detail: other.map((x) => `${x.shot}: ${x.reason}`).join("\n"),
    });
  }
  if (!out.length && !r.errors?.length) out.push({ severity: "info", summary: "Nothing to queue", detail: "" });
  return out;
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
      for (const t of renderReport(r)) host().toast(t.severity, t.summary, t.detail || undefined);
      for (const x of r.errors ?? []) host().toast("error", `${x.shot} didn't queue`, x.error);
      const warnings = renderWarnings(r);
      if (warnings.length) {
        host().toast(
          "warn",
          warnings.length === 1 ? `${warnings[0].shot ? `${warnings[0].shot}: ` : ""}render warning` : `${warnings.length} render warnings`,
          warnings.map((w) => (w.shot && !w.text.startsWith(w.shot) ? `${w.shot}: ${w.text}` : w.text)).join("\n"),
        );
      }
      scheduleRefresh(0);
      return r;
    } catch (e) {
      report("Render request failed", e);
      return undefined;
    }
  });
}

export function renderShots(shots: string[], redo = false, allowMissingRefs = false, target: string | null = null) {
  const s = get();
  if (!s.ep || !shots.length) return Promise.resolve(undefined);
  return queueRender({ ...baseRender(s.ep, s.pass, shots, allowMissingRefs, target), redo });
}

/**
 * Render with a look first: if any of `shots` is missing refs, open the render
 * dialog (which lists what will be skipped and offers "render anyway");
 * otherwise queue straight away (confirming big batches).
 */
export function requestRender(shots: string[], redo = false, title?: string) {
  const s = get();
  const st = s.ep ? s.status[statusKey(s.ep, s.pass)] : undefined;
  if (!s.ep || !shots.length) return;
  const { blocked } = splitByMissingRefs(st?.shots ?? [], shots);
  if (blocked.length) {
    set({ renderAsk: { shots, pass: s.pass, redo, title: title ?? `Render ${shots.length === 1 ? shots[0] : `${shots.length} shots`}` }, menu: null });
    return;
  }
  if (shots.length > 8 && !confirm(`Queue ${shots.length} ${s.pass} renders?`)) return;
  void renderShots(shots, redo);
}

export function closeRenderAsk() {
  set({ renderAsk: null });
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
  /** send allow_missing_refs: true */
  allowMissingRefs?: boolean;
  /** send allow_model_mismatch: true */
  allowModelMismatch?: boolean;
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
  /** Phase 8: the target for this run (sent as `target`); null/absent = the shot's own */
  target?: string | null;
  /** The prompt isn't the user's to set: the shot is retargeted, or this run is on
   * another target (its prompt is compiled at queue time). Sent as null, never saved. */
  lockPrompt?: boolean;
}

/** What to send for a redo: the override to write first (if any) and the render. */
export function planRedo(p: RedoPlan, ep: string, d: ShotDetail): { override: OverrideFields | null; render: RenderRequest } {
  const seedMode: SeedMode = p.seed.mode === "new" ? "new" : "auto";
  const seed: Seed | null = p.seed.mode === "new" ? null : p.seed.seed;
  const current = d.target || d.built_target || null;
  const target = p.target && p.target !== current ? p.target : null;
  const base = { ...baseRender(ep, p.pass, [p.shot], !!p.allowMissingRefs, target, !!p.allowModelMismatch), redo: true, parent_take: p.parent, note: p.note, seed_mode: seedMode, seed };
  const prompt = p.lockPrompt ? null : p.prompt;
  // a one-off run on another target isn't saved: the override is the shot's own target's
  if (!p.saveAsOverride || target) {
    return {
      override: null,
      render: { ...base, model: p.model || null, loras: p.loras, steps: p.steps, prompt },
    };
  }
  // The override then carries the settings, so the take records them as overrides.
  // Only changed fields are written: re-sending an unchanged prompt would re-stamp
  // its base_hash and silently clear an "override stale" warning.
  const f: OverrideFields = {};
  const eff = d.effective;
  if (!p.lockPrompt && p.prompt !== eff.prompt) f.prompt = p.prompt === d.built_prompt ? null : p.prompt;
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
    if (get().refPrompts[pid]) scheduleRefsRefresh();
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
    const rr = pid ? get().refPrompts[pid] : undefined;
    if (rr) host().toast("error", `${refLabel(rr.ref, rr.view)} ${tn(rr.take)} failed in ComfyUI`, [e.node_type, e.exception_message].filter(Boolean).join(": "));
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
    if (get().ep && get().refs[get().ep!]) scheduleRefsRefresh();
  });
  h.on("h3pipe.ref", (d) => {
    const r = (d ?? {}) as RefEvent;
    if (!sameEp(r.ep, get().ep)) return;
    const tracked = Object.values(get().refPrompts).some((x) => x.ref === r.ref && (x.view ?? null) === (r.view ?? null) && x.take === r.take);
    if (tracked && r.status === "ok") host().toast("success", `${refLabel(r.ref, r.view)} ${tn(r.take)} ready`);
    if (tracked && r.status === "failed") host().toast("error", `${refLabel(r.ref, r.view)} ${tn(r.take)} failed`);
    scheduleRefsRefresh();
  });
}

function isTracked(t: TakeEvent): boolean {
  return Object.values(get().prompts).some((r) => r.pass === t.pass && r.shot === t.shot && r.take === t.take);
}

// ---------------------------------------------------------------------------
// the floating inspector
// ---------------------------------------------------------------------------

/** Open the inspector window; with a shot, select it too. It follows the selection. */
export function openInspector(shot?: string | null, take?: number | null) {
  if (shot) select(shot, take ?? (get().shot === shot ? get().take : null));
  set({ inspector: true, menu: null });
}

export function closeInspector() {
  set({ inspector: false });
}

// ---------------------------------------------------------------------------
// Play all
// ---------------------------------------------------------------------------

let plCache: { a?: EpisodeStatus; b?: EpisodeStatus; items: PlayItem[] } = { items: [] };

/** The current pass's cut as a playlist (memoised on the status objects). */
export function currentPlaylist(s: AppState = get()): PlayItem[] {
  if (!s.ep) return [];
  const st = s.status[statusKey(s.ep, s.pass)];
  const other = s.status[statusKey(s.ep, s.pass === "proxy" ? "final" : "proxy")];
  if (plCache.a !== st || plCache.b !== other) plCache = { a: st, b: other, items: buildPlaylist(st, other) };
  return plCache.items;
}

function nextSeek(t: number) {
  return { t, n: (get().cutPlay.seek?.n ?? 0) + 1 };
}

/** Play the cut in the viewer, from the start or from `fromShot`. */
export function playAll(fromShot?: string | null) {
  const s = get();
  if (!s.ep) return;
  const items = currentPlaylist(s);
  if (!items.length) {
    host().toast("info", "Nothing to play", "The cut has no shots.");
    return;
  }
  const resume = s.viewer?.kind === "cut" && s.cutPlay.pos < totalDuration(items) - 0.05;
  const t = fromShot ? startOf(items, fromShot) ?? 0 : resume ? s.cutPlay.pos : 0;
  set({
    viewer: { kind: "cut", shot: fromShot ?? items[0].shot, pass: s.pass, a: null, b: null, mode: "single", target: "a" },
    menu: null,
    cutPlay: { playing: true, pos: t, seek: nextSeek(t) },
  });
  // placeholders need the other pass's takes
  const op: Pass = s.pass === "proxy" ? "final" : "proxy";
  const st = s.status[statusKey(s.ep, s.pass)];
  if (st?.shots.some((x) => x.cut.placeholder) && !s.status[statusKey(s.ep, op)]) void refreshEpisode(s.ep, op);
}

export function seekCut(t: number) {
  const s = get();
  const total = totalDuration(currentPlaylist(s));
  const c = Math.min(Math.max(0, t), total);
  set({ cutPlay: { ...s.cutPlay, pos: c, seek: nextSeek(c) } });
}

/** Jump the open cut player to a shot. Returns false when the player isn't open. */
export function seekToShot(shot: string): boolean {
  const s = get();
  if (s.viewer?.kind !== "cut") return false;
  const t = startOf(currentPlaylist(s), shot);
  if (t == null) return false;
  seekCut(t);
  return true;
}

export function setCutPlaying(playing: boolean) {
  const s = get();
  if (s.viewer?.kind !== "cut") {
    if (playing) playAll();
    return;
  }
  const total = totalDuration(currentPlaylist(s));
  // playing again from the end starts over
  if (playing && s.cutPlay.pos >= total - 0.02) {
    set({ cutPlay: { playing: true, pos: 0, seek: nextSeek(0) } });
    return;
  }
  set({ cutPlay: { ...s.cutPlay, playing } });
}

export function toggleCutPlay() {
  const s = get();
  setCutPlaying(!(s.viewer?.kind === "cut" && s.cutPlay.playing));
}

/** Written by the player as it plays: the playhead, and the shot on screen. */
export function reportCutPos(pos: number, shot: string | null, ended = false) {
  const s = get();
  const patch: Partial<AppState> = {};
  if (Math.abs(s.cutPlay.pos - pos) > 1e-4 || (ended && s.cutPlay.playing)) {
    patch.cutPlay = { ...s.cutPlay, pos, playing: ended ? false : s.cutPlay.playing };
  }
  if (shot && s.viewer?.kind === "cut" && s.viewer.shot !== shot) patch.viewer = { ...s.viewer, shot };
  if (Object.keys(patch).length) set(patch);
}

// ---------------------------------------------------------------------------
// folder browser: roots, episodes, files to import
// ---------------------------------------------------------------------------

export function openBrowse(b: BrowseState) {
  set({ browse: b, menu: null });
}

export function closeBrowse() {
  set({ browse: null });
}

export async function addRoot(path: string): Promise<boolean> {
  const roots = get().config?.roots ?? [];
  if (roots.some((r) => sameDir(r, path))) {
    host().toast("info", "Already a root", path);
    return true;
  }
  const ok = await saveRoots([...roots, path]);
  if (ok) host().toast("success", "Added a project root", path);
  return ok;
}

export async function removeRoot(path: string): Promise<boolean> {
  const roots = get().config?.roots ?? [];
  return saveRoots(roots.filter((r) => !sameDir(r, path)));
}

/**
 * Open an episode picked in the browser. If the roots don't reach it (episodes
 * are found up to two levels below a root), its parent folder becomes a root.
 */
export async function openEpisodeAt(ep: string, parent: string | null): Promise<boolean> {
  const find = () => get().episodes?.find((e) => sameEp(e.ep, ep));
  let hit = find();
  if (!hit) {
    const roots = get().config?.roots ?? [];
    if (!reachable(ep, roots)) {
      const root = parent || ep;
      if (!(await saveRoots([...roots, root]))) return false;
      host().toast("info", "Added a project root", root);
    } else {
      await loadEpisodes();
    }
    hit = find();
  }
  if (!hit) {
    host().toast("error", "That folder isn't listed as an episode", `${ep}\nAn episode needs a series.json (here or in its parent) and a script.`);
    return false;
  }
  selectEpisode(hit.ep);
  return true;
}

// ---------------------------------------------------------------------------
// references (Phase 5)
// ---------------------------------------------------------------------------

/** set once the Refs tab has been shown, so episode switches load refs too */
let refsWanted = false;
const refsInflight = new Map<string, Promise<Ref[] | undefined>>();

export function refLabel(ref: string, view?: string | null): string {
  const ep = get().ep;
  const r = ep ? get().refs[ep]?.find((x) => x.id === ref) : undefined;
  const name = r?.name ?? ref.replace(/^[a-z]+:/, "");
  return view ? `${name} (${viewLabel(view)})` : name;
}

export function loadRefs(ep = get().ep): Promise<Ref[] | undefined> {
  refsWanted = true;
  if (!ep) return Promise.resolve(undefined);
  const running = refsInflight.get(ep);
  if (running) return running;
  const p = (async () => {
    set((s) => ({ refsLoading: { ...s.refsLoading, [ep]: true } }));
    try {
      const { refs } = await api().refs(ep);
      set((s) => {
        const refsError = { ...s.refsError };
        delete refsError[ep];
        return { refs: { ...s.refs, [ep]: refs }, refsError };
      });
      learnRefPrompts(ep, refs);
      return refs;
    } catch (e) {
      set((s) => ({ refsError: { ...s.refsError, [ep]: errText(e) } }));
      return undefined;
    } finally {
      set((s) => {
        const refsLoading = { ...s.refsLoading };
        delete refsLoading[ep];
        return { refsLoading };
      });
      refsInflight.delete(ep);
    }
  })();
  refsInflight.set(ep, p);
  return p;
}

/** Queued ref takes that carry a prompt id (if the server sends it). */
function learnRefPrompts(ep: string, refs: Ref[]) {
  const learned: Record<string, RefTakeRef> = {};
  for (const r of refs) {
    const lists: [string | null, RefTake[]][] = [[null, r.takes], ...(r.views ?? []).map((v): [string, RefTake[]] => [v.view, v.takes])];
    for (const [view, takes] of lists) {
      for (const t of takes) if (t.status === "queued" && t.comfy_prompt_id) learned[t.comfy_prompt_id] = { ep, ref: r.id, view, take: t.take };
    }
  }
  if (Object.keys(learned).length) set((s) => ({ refPrompts: { ...s.refPrompts, ...learned } }));
}

let refsTimer: ReturnType<typeof setTimeout> | null = null;

export function scheduleRefsRefresh(delay = 200) {
  if (refsTimer) clearTimeout(refsTimer);
  refsTimer = setTimeout(() => {
    refsTimer = null;
    if (get().ep) void loadRefs();
  }, delay);
}

export function setRefsFilter(refsFilter: RefFilter) {
  set({ refsFilter });
}

export function toggleRefOpen(id: string, open?: boolean) {
  set((s) => ({ refOpen: { ...s.refOpen, [id]: open ?? !s.refOpen[id] } }));
}

export function selectRefTake(ref: string, view: string | null, take: number | null) {
  set({ refSel: take == null ? null : { ref, view, take } });
}

/** Open the Refs tab on the refs a shot (or the whole episode) is missing. */
export function showMissingRefs(shot?: string | null) {
  const s = get();
  const st = s.ep ? s.status[statusKey(s.ep, s.pass)] : undefined;
  const paths = new Set((st?.shots ?? []).filter((x) => !shot || x.shot === shot).flatMap((x) => (x.missing_refs ?? []).map((m) => normPath(m.path))));
  set({ menu: null });
  host().show("refs");
  void loadRefs().then((refs) => {
    const open: Record<string, boolean> = {};
    for (const r of refs ?? []) if (paths.has(normPath(r.path))) open[r.id] = true;
    set((x) => ({ refsFilter: "missing", refOpen: { ...x.refOpen, ...open } }));
  });
}

export async function generateRef(req: Omit<RefGenerateRequest, "ep">): Promise<boolean> {
  const ep = get().ep;
  if (!ep) return false;
  return withBusy(`refgen|${req.ref}`, async () => {
    try {
      const r = await api().refsGenerate({ ...req, ep });
      const learned: Record<string, RefTakeRef> = {};
      for (const q of r.queued) learned[q.prompt_id] = { ep, ref: q.ref, view: q.view ?? null, take: q.take };
      set((s) => ({ refPrompts: { ...s.refPrompts, ...learned } }));
      if (r.queued.length) {
        host().toast("success", `Queued ${r.queued.length} candidate${r.queued.length > 1 ? "s" : ""}`, r.queued.map((q) => `${refLabel(q.ref, q.view)} ${tn(q.take)}`).join(", "));
      }
      for (const x of r.errors) host().toast("error", `${refLabel(x.ref ?? req.ref, x.view)} didn't queue`, x.error);
      scheduleRefsRefresh(0);
      return r.queued.length > 0;
    } catch (e) {
      report(`Couldn't generate ${refLabel(req.ref, req.view)}`, e);
      return false;
    }
  });
}

/**
 * Queue one candidate for every ref this episode is missing (lib/refs
 * missingPlan). Each lands live through the server's auto-pick as it finishes.
 * Asks first: it can be a lot of GPU work.
 */
export async function generateMissing(): Promise<void> {
  const s = get();
  const ep = s.ep;
  if (!ep) return;
  const plan = missingPlan(s.refs[ep] ?? [], s.pass);
  if (!plan.length) {
    host().toast("info", "Nothing to generate", "Every missing ref already has a candidate queued or waiting.");
    return;
  }
  const views = plan.reduce((n, p) => n + (p.view == null && isCharacterRef(s.refs[ep], p.ref) ? 4 : 1), 0);
  if (!confirm(`Generate ${plan.length} missing ref${plan.length > 1 ? "s" : ""} (${views} image${views > 1 ? "s" : ""}) on ComfyUI?\n\n`
    + plan.map((p) => `• ${p.label}`).join("\n")
    + "\n\nEach becomes live automatically when it finishes (you can pick another candidate later).")) return;
  await withBusy("refgen|missing", async () => {
    let queued = 0;
    const failed: string[] = [];
    const learned: Record<string, RefTakeRef> = {};
    for (const p of plan) {
      try {
        const r = await api().refsGenerate({
          ep, ref: p.ref, view: p.view, count: 1, seed_mode: "auto", seed: null,
          prompt: null, model: null, loras: null, steps: null, note: "generate missing",
        });
        for (const q of r.queued) learned[q.prompt_id] = { ep, ref: q.ref, view: q.view ?? null, take: q.take };
        queued += r.queued.length;
        for (const x of r.errors) failed.push(`${p.label}: ${x.error}`);
      } catch (e) {
        failed.push(`${p.label}: ${errText(e)}`);
      }
    }
    set((st) => ({ refPrompts: { ...st.refPrompts, ...learned } }));
    if (queued) host().toast("success", `Queued ${queued} image${queued > 1 ? "s" : ""} for ${plan.length - failed.length} missing ref${plan.length - failed.length === 1 ? "" : "s"}`, "They go live as they finish.");
    if (failed.length) host().toast("error", `${failed.length} didn't queue`, failed.join("\n"));
    scheduleRefsRefresh(0);
  });
}

function isCharacterRef(refs: Ref[] | undefined, id: string): boolean {
  return !!refs?.find((r) => r.id === id && hasViews(r));
}

function replaceRef(ep: string, ref: Ref) {
  set((s) => {
    const list = s.refs[ep];
    if (!list) return {};
    return { refs: { ...s.refs, [ep]: list.map((r) => (r.id === ref.id ? ref : r)) } };
  });
}

export async function pickRef(ref: string, view: string | null, take: number): Promise<boolean> {
  const ep = get().ep;
  if (!ep) return false;
  return withBusy(`refpick|${ref}`, async () => {
    try {
      const r = await api().refsPick({ ep, ref, view, take });
      const isRef = !!r && typeof r === "object" && "id" in r;
      if (isRef) replaceRef(ep, r);
      const sheet = !!view && isRef && !!r.views && r.views.length >= 4 && r.views.every((v) => v.picked != null);
      host().toast("success", `${refLabel(ref, view)}: ${tn(take)} is live`, sheet ? "All four views are picked: the sheet is stitched." : undefined);
      // the live file changed: missing refs and ref-stale takes follow
      scheduleRefsRefresh(0);
      scheduleRefresh(0);
      return true;
    } catch (e) {
      report(`Couldn't pick ${refLabel(ref, view)} ${tn(take)}`, e);
      return false;
    }
  });
}

/**
 * A shot's first (or last) keyframe from a frame of a video take
 * (POST /h3pipe/refs/keyframe). With no source: the neighbouring shot in the
 * cut (previous for first, next for last) and the take its cut entry uses.
 */
export async function keyframeFromTake(opts: {
  shot: string;
  which?: KeyframeEnd;
  sourceShot?: string | null;
  sourceTake?: number | null;
  frame?: number | "first" | "last" | null;
  pass?: Pass;
}): Promise<Ref | null> {
  const ep = get().ep;
  if (!ep) return null;
  const which = opts.which ?? "first";
  const pass = opts.pass ?? get().pass;
  return withBusy(`keyframe|${opts.shot}|${which}`, async () => {
    try {
      const r = await api().refsKeyframe({
        ep, pass, shot: opts.shot, which,
        source_shot: opts.sourceShot ?? null, source_take: opts.sourceTake ?? null, frame: opts.frame ?? null,
      });
      upsertRef(ep, r);
      const t = r.takes[r.takes.length - 1];
      const from = keyframeSource(t);
      const live = !!t && r.picked === t.take;
      host().toast(
        "success",
        `${opts.shot}: ${which} frame${from ? ` from ${from}` : ""}`,
        live
          ? `${tn(t.take)} is the live ${which} keyframe.`
          : `Added as ${tn(t?.take)}; ${opts.shot} already has a ${which} keyframe (${tn(r.picked)}). Pick the new one in the Refs tab to use it.`,
      );
      // the live keyframe may have changed: takes that used the old one go ref-stale
      scheduleRefsRefresh(0);
      scheduleRefresh(0);
      return r;
    } catch (e) {
      report(`Couldn't make ${opts.shot}'s ${which} keyframe`, e);
      return null;
    }
  });
}

/** Open the Refs tab on a shot's keyframes. */
export function openKeyframesInRefs(shot: string) {
  set((s) => ({
    menu: null,
    refsFilter: s.refsFilter === "missing" ? "episode" : s.refsFilter,
    refOpen: { ...s.refOpen, [keyframeRefId(shot, "first")]: true, [keyframeRefId(shot, "last")]: true },
  }));
  host().show("refs");
  void loadRefs();
}

/** A ref from a route: replace it in the list, or add it (a new keyframe). */
function upsertRef(ep: string, ref: Ref) {
  if (!ref || typeof ref !== "object" || !("id" in ref)) return;
  set((s) => {
    const list = s.refs[ep];
    if (!list) return {};
    const has = list.some((r) => r.id === ref.id);
    return { refs: { ...s.refs, [ep]: has ? list.map((r) => (r.id === ref.id ? ref : r)) : [...list, ref] } };
  });
}

export async function importRef(ref: string, view: string | null, sourcePath: string): Promise<boolean> {
  const ep = get().ep;
  if (!ep) return false;
  return withBusy(`refimport|${ref}`, async () => {
    try {
      const t = await api().refsImport({ ep, ref, view, source_path: sourcePath });
      host().toast("success", `Imported into ${refLabel(ref, view)}`, `${tn(t?.take)} from ${sourcePath}. Pick it to make it live.`);
      await loadRefs(ep);
      if (t?.take != null) selectRefTake(ref, view, t.take);
      return true;
    } catch (e) {
      report(`Couldn't import into ${refLabel(ref, view)}`, e);
      return false;
    }
  });
}

export async function saveRefOverride(ref: string, fields: OverrideFields, view: string | null = null): Promise<boolean> {
  const ep = get().ep;
  if (!ep) return false;
  if (!Object.keys(fields).length) return true;
  return withBusy(`refoverride|${ref}`, async () => {
    try {
      await api().putRefOverride({ ep, ref, view, fields });
      host().toast("success", `${refLabel(ref, view)}: settings saved`, Object.keys(fields).join(", "));
      await loadRefs(ep);
      return true;
    } catch (e) {
      report(`Couldn't save ${refLabel(ref, view)}'s settings`, e);
      return false;
    }
  });
}

export async function revertRefOverride(ref: string, view: string | null = null) {
  const ep = get().ep;
  if (!ep) return;
  return withBusy(`refoverride|${ref}`, async () => {
    try {
      await api().deleteRefOverride(ep, ref, view);
      host().toast("info", `${refLabel(ref, view)}: back to the series config's settings`);
      await loadRefs(ep);
    } catch (e) {
      report(`Couldn't revert ${refLabel(ref, view)}`, e);
    }
  });
}

/** Compare a ref's candidates (stills) in the viewer. */
export function openImageCompare(ref: string, view: string | null, a: number | null, b: number | null = null) {
  set({
    viewer: { kind: "image", shot: "", pass: get().pass, a, b, mode: b != null ? "side" : "single", target: b != null ? "b" : "a", ref, view },
    menu: null,
  });
}
