// Phase 9b: editing the cut from the timeline (order, trims, locks), undo /
// redo, and play-through (seek from the ruler, J / K / L, the recording).
// Each edit is one PUT /h3pipe/cut of the whole pass's list, shown at once
// (optimistic) and rolled back if the server refuses it.

import { ApiError } from "./api";
import {
  currentPlaylist, openIssue, playAll, refreshEpisode, report, seekCut, setCutPlaying, setStatusHook,
} from "./actions";
import { api, host } from "./host";
import { audioOf, audioWhy, normalizeAudio, sameAudio } from "./lib/audioSource";
import {
  applyEntries, clampTrim, clearAudio, clearTrims, entriesOf, fieldsOf, moveTo, nudge, sameEntries, scriptOrder, withFields,
} from "./lib/cutEdit";
import { PeaksCache } from "./lib/peaks";
import { locate, totalDuration } from "./lib/playlist";
import { UndoStack, applyEdit, diffEdit, type CutEdit } from "./lib/undo";
import { statusKey, store, type CutAudio } from "./store";
import type { CutAudioSource, CutEntry, CutWhat, EpisodeStatus, Pass } from "./types";

const set = store.set;
const get = store.get;

// ---------------------------------------------------------------------------
// undo stacks (one per episode and pass, for the session)
// ---------------------------------------------------------------------------

const stacks = new Map<string, UndoStack>();

export function undoStack(ep: string, pass: Pass): UndoStack {
  const k = statusKey(ep, pass);
  let s = stacks.get(k);
  if (!s) {
    s = new UndoStack();
    stacks.set(k, s);
  }
  return s;
}

function syncUndo(ep: string, pass: Pass) {
  const s = undoStack(ep, pass);
  const k = statusKey(ep, pass);
  set((st) => ({ cutUndo: { ...st.cutUndo, [k]: { undo: s.undoLabel, redo: s.redoLabel } } }));
}

/** For tests: forget every stack. */
export function resetCutHistory() {
  stacks.clear();
  set({ cutUndo: {} });
}

// ---------------------------------------------------------------------------
// saving: one PUT per edit, in order, shown before the server answers
// ---------------------------------------------------------------------------

let chain: Promise<unknown> = Promise.resolve();

/** The latest entries being saved, by statusKey: laid over any status fetched meanwhile. */
const inFlight = new Map<string, { entries: CutEntry[]; n: number }>();
let seq = 0;
setStatusHook((ep, pass, st) => {
  const f = inFlight.get(statusKey(ep, pass));
  return f ? applyEntries(st, f.entries) : st;
});

/** Resolves once every cut save started so far has finished (tests, and before a server-side edit). */
export function cutSettled(): Promise<void> {
  return chain.then(() => undefined, () => undefined);
}

/**
 * Show `entries` as the pass's cut now and PUT them. On an error the status
 * goes back to what it was (and is refetched: the server is the truth).
 */
function save(ep: string, pass: Pass, prev: EpisodeStatus, entries: CutEntry[], what: string): Promise<boolean> {
  const key = statusKey(ep, pass);
  const optimistic = applyEntries(prev, entries);
  set((s) => ({ status: { ...s.status, [key]: optimistic } }));
  const n = ++seq;
  inFlight.set(key, { entries, n });
  const done = () => {
    if (inFlight.get(key)?.n === n) inFlight.delete(key);
  };
  const run = chain.then(async () => {
    try {
      await api().putCut(ep, pass, entries);
      done();
      return true;
    } catch (e) {
      done();
      if (get().status[key] === optimistic) set((s) => ({ status: { ...s.status, [key]: prev } }));
      report(`Couldn't save the cut (${what})`, e);
      void refreshEpisode(ep, pass);
      return false;
    }
  });
  chain = run;
  return run;
}

interface Ctx {
  ep: string;
  pass: Pass;
  st: EpisodeStatus;
}

function ctx(): Ctx | null {
  const s = get();
  if (!s.ep) return null;
  const st = s.status[statusKey(s.ep, s.pass)];
  return st ? { ep: s.ep, pass: s.pass, st } : null;
}

/**
 * One undoable edit: `change` turns the pass's entries into the new ones
 * (null: nothing to do). Returns whether it was saved.
 */
export async function editCut(label: string, change: (entries: CutEntry[], st: EpisodeStatus) => CutEntry[] | null): Promise<boolean> {
  const c = ctx();
  if (!c) return false;
  const before = entriesOf(c.st);
  const after = change(before, c.st);
  if (!after || sameEntries(before, after)) return false;
  const edit = diffEdit(label, before, after);
  const ok = await save(c.ep, c.pass, c.st, after, label.toLowerCase());
  if (ok && edit) {
    undoStack(c.ep, c.pass).push(edit);
    syncUndo(c.ep, c.pass);
  }
  return ok;
}

async function step(dir: "undo" | "redo"): Promise<boolean> {
  const c = ctx();
  if (!c) return false;
  const stack = undoStack(c.ep, c.pass);
  const edit: CutEdit | undefined = dir === "undo" ? stack.takeUndo() : stack.takeRedo();
  if (!edit) {
    host().toast("info", dir === "undo" ? "Nothing to undo" : "Nothing to redo", `Cut edits in this session (${c.pass} pass).`);
    return false;
  }
  syncUndo(c.ep, c.pass);
  const entries = applyEdit(entriesOf(c.st), edit, dir);
  const ok = sameEntries(entries, entriesOf(c.st)) || (await save(c.ep, c.pass, c.st, entries, `${dir} ${edit.label.toLowerCase()}`));
  if (!ok) {
    if (dir === "undo") stack.restoreUndo(edit);
    else stack.restoreRedo(edit);
    syncUndo(c.ep, c.pass);
    return false;
  }
  host().toast("info", `${dir === "undo" ? "Undid" : "Redid"}: ${edit.label}`);
  return true;
}

export const undoCut = () => step("undo");
export const redoCut = () => step("redo");

// ---------------------------------------------------------------------------
// the edits
// ---------------------------------------------------------------------------

function isLocked(st: EpisodeStatus, shot: string): boolean {
  return !!st.shots.find((s) => s.shot === shot)?.cut?.locked;
}

function refuseLocked(shot: string, what: string) {
  host().toast("warn", `${shot} is locked`, `A locked clip can't be ${what}. Unlock it from its menu first.`);
}

/** Move a clip to insertion point `gap` (0..n in the current cut order). */
export function moveClip(shot: string, gap: number): Promise<boolean> {
  const c = ctx();
  if (!c) return Promise.resolve(false);
  if (isLocked(c.st, shot)) {
    refuseLocked(shot, "moved");
    return Promise.resolve(false);
  }
  return editCut(`Move ${shot}`, (list) => moveTo(list, shot, gap));
}

/** Alt+← / Alt+→: move a clip one place. */
export function nudgeClip(shot: string, delta: -1 | 1): Promise<boolean> {
  const c = ctx();
  if (!c) return Promise.resolve(false);
  if (isLocked(c.st, shot)) {
    refuseLocked(shot, "moved");
    return Promise.resolve(false);
  }
  return editCut(`Move ${shot} ${delta < 0 ? "left" : "right"}`, (list) => nudge(list, shot, delta));
}

/** The clip's untrimmed length in the cut's frames (null: unknown), from Play all's list. */
export function clipTotal(shot: string): number | null {
  return currentPlaylist().find((i) => i.shot === shot)?.total ?? null;
}

/** Set a clip's trims (frames of the cut), each clamped so at least one frame stays. */
export function setTrims(shot: string, trimIn: number, trimOut: number, label = `Trim ${shot}`): Promise<boolean> {
  const c = ctx();
  if (!c) return Promise.resolve(false);
  if (isLocked(c.st, shot)) {
    refuseLocked(shot, "trimmed");
    return Promise.resolve(false);
  }
  const total = clipTotal(shot);
  return editCut(label, (list) => {
    const f = fieldsOf(list.find((e) => e.shot === shot));
    let a = Math.max(0, Math.round(trimIn));
    let b = Math.max(0, Math.round(trimOut));
    // the side being changed gives way to the other
    if (a !== f.trim_in) {
      b = clampTrim("out", b, total, 0, 0);
      a = clampTrim("in", a, total, 0, b);
    } else {
      a = clampTrim("in", a, total, 0, 0);
      b = clampTrim("out", b, total, a, 0);
    }
    return withFields(list, shot, { trim_in: a, trim_out: b });
  });
}

// ---------------------------------------------------------------------------
// Phase 9d: a clip's audio from elsewhere
// ---------------------------------------------------------------------------

/**
 * Set (or, with null, clear) a clip's audio source. One more cut edit, so it
 * is optimistic, undoable and part of the history like a trim.
 */
export function setClipAudio(shot: string, audio: CutAudioSource | null, label?: string): Promise<boolean> {
  const c = ctx();
  if (!c) return Promise.resolve(false);
  if (isLocked(c.st, shot)) {
    refuseLocked(shot, "given another audio source");
    return Promise.resolve(false);
  }
  const want = normalizeAudio(audio, shot);
  const text = label ?? (want ? `Audio of ${shot} from ${audioWhy(want, shot)}` : `${shot} back to its own audio`);
  return editCut(text, (list) => withFields(list, shot, { audio: want }));
}

/** The Inspector's and the menu's Clear: back to the clip's own take's sound. */
export function clearClipAudio(shot: string): Promise<boolean> {
  return setClipAudio(shot, null);
}

/** Whether a clip already plays something other than its own sound. */
export function clipAudioOf(shot: string, pass: Pass = get().pass): CutAudioSource | null {
  const s = get();
  if (!s.ep) return null;
  return audioOf(s.status[statusKey(s.ep, pass)]?.shots.find((x) => x.shot === shot)?.cut);
}

/** True when `audio` is what the clip already plays (the window's Save is then idle). */
export function clipAudioUnchanged(shot: string, audio: CutAudioSource | null): boolean {
  return sameAudio(clipAudioOf(shot), audio);
}

export function toggleLock(shot: string, locked?: boolean): Promise<boolean> {
  const c = ctx();
  if (!c) return Promise.resolve(false);
  const on = locked ?? !isLocked(c.st, shot);
  return editCut(on ? `Lock ${shot}` : `Unlock ${shot}`, (list) => withFields(list, shot, { locked: on }));
}

/**
 * I / O: trim the clip under the Play all playhead so it starts (I) or ends
 * (O) at the frame on screen. The playhead stays on that frame.
 */
export async function trimAtPlayhead(side: "in" | "out"): Promise<boolean> {
  const s = get();
  const c = ctx();
  if (!c) return false;
  if (s.viewer?.kind !== "cut") {
    host().toast("info", `${side === "in" ? "I" : "O"} trims at the Play all playhead`, "Open Play all (or click the ruler) and stop on the frame first.");
    return false;
  }
  const items = currentPlaylist(s);
  const { index, offset } = locate(items, s.cutPlay.pos);
  const it = items[index];
  if (!it) return false;
  const fps = c.st.fps || 24;
  const frames = Math.round(offset * fps);
  const left = Math.round(it.dur * fps) - frames;
  if (side === "in") {
    if (frames <= 0) return false;
    const ok = await setTrims(it.shot, it.trimIn + frames, it.trimOut, `Trim ${it.shot} in (I)`);
    if (ok) seekCut(currentPlaylist().find((x) => x.shot === it.shot)?.start ?? it.start);
    return ok;
  }
  if (left <= 0) return false;
  return setTrims(it.shot, it.trimIn, it.trimOut + left, `Trim ${it.shot} out (O)`);
}

/**
 * Menu: back to script order and/or no trims (POST /h3pipe/cut/reset), or
 * the other pass's order and/or trims (POST /h3pipe/cut/copy). Undoable like
 * any edit: the change is read back from the refreshed status.
 */
async function serverEdit(label: string, run: (c: Ctx) => Promise<unknown>, fallback: ((list: CutEntry[], st: EpisodeStatus) => CutEntry[] | null) | null): Promise<boolean> {
  const c = ctx();
  if (!c) return false;
  await cutSettled();
  const before = entriesOf(get().status[statusKey(c.ep, c.pass)] ?? c.st);
  try {
    await run(c);
  } catch (e) {
    // an older server without the route: do it here when we can
    if (fallback && e instanceof ApiError && (e.status === 404 || e.status === 405)) return editCut(label, fallback);
    report(`Couldn't ${label.toLowerCase()}`, e);
    return false;
  }
  const st = await refreshEpisode(c.ep, c.pass);
  if (!st) return true;
  const edit = diffEdit(label, before, entriesOf(st));
  if (edit) {
    undoStack(c.ep, c.pass).push(edit);
    syncUndo(c.ep, c.pass);
    host().toast("success", label);
  } else {
    host().toast("info", `${label}: nothing to change`);
  }
  return true;
}

const RESET_LABEL: Record<CutWhat, string> = {
  order: "Reset order",
  trims: "Clear trims",
  audio: "Clear audio sources",
  all: "Reset order, trims and audio",
};

export function resetCut(what: CutWhat): Promise<boolean> {
  return serverEdit(RESET_LABEL[what] ?? "Reset the cut", (c) => api().cutReset(c.ep, c.pass, what), (list, st) => {
    let out = list;
    if (what === "order" || what === "all") {
      const order = scriptOrder(st);
      if (!order) return null;
      const orphans = list.filter((e) => !order.includes(e.shot));
      out = [...order.map((s) => out.find((e) => e.shot === s)!).filter(Boolean), ...orphans];
    }
    if (what === "trims" || what === "all") out = clearTrims(out);
    if (what === "audio" || what === "all") out = clearAudio(out);
    return out;
  });
}

const COPY_LABEL: Record<CutWhat, string> = {
  order: "order",
  trims: "trims",
  audio: "audio sources",
  all: "order, trims and audio",
};

export function copyCut(what: CutWhat): Promise<boolean> {
  const c = ctx();
  if (!c) return Promise.resolve(false);
  const from: Pass = c.pass === "proxy" ? "final" : "proxy";
  const label = `Copy ${COPY_LABEL[what] ?? what} from ${from}`;
  return serverEdit(label, (x) => api().cutCopy(x.ep, from, x.pass, what), null);
}

// ---------------------------------------------------------------------------
// play-through
// ---------------------------------------------------------------------------

/** Seek the cut to `t`; with no Play all open, open it paused there. */
export function seekCutAt(t: number) {
  const s = get();
  if (!s.ep) return;
  const items = currentPlaylist(s);
  if (!items.length) return;
  const c = Math.min(Math.max(0, t), totalDuration(items));
  if (s.viewer?.kind === "cut") {
    seekCut(c);
    return;
  }
  const { index } = locate(items, c);
  set({
    viewer: { kind: "cut", shot: items[index]?.shot ?? items[0].shot, pass: s.pass, a: null, b: null, mode: "single", target: "a" },
    menu: null,
    cutPlay: { playing: false, pos: c, seek: { t: c, n: (s.cutPlay.seek?.n ?? 0) + 1 }, rate: 1 },
  });
}

const SPEEDS = [1, 2, 4];

/** J / K / L: shuttle backwards, stop, forwards; pressing J or L again goes faster. */
export function shuttle(key: "j" | "k" | "l") {
  const s = get();
  const open = s.viewer?.kind === "cut";
  const rate = s.cutPlay.rate ?? 1;
  if (key === "k") {
    if (open) set({ cutPlay: { ...s.cutPlay, playing: false, rate: 1 } });
    return;
  }
  const dir = key === "l" ? 1 : -1;
  const playing = open && s.cutPlay.playing;
  let next = dir;
  if (playing && Math.sign(rate) === dir) {
    const i = SPEEDS.indexOf(Math.abs(rate));
    next = dir * SPEEDS[Math.min(SPEEDS.length - 1, (i < 0 ? 0 : i) + 1)];
  }
  if (!open) {
    if (dir > 0) {
      playAll();
      return;
    }
    seekCutAt(s.cutPlay.pos);
  }
  const now = get();
  set({ cutPlay: { ...now.cutPlay, playing: true, rate: next } });
}

export function setCutAudio(cutAudio: CutAudio) {
  set({ cutAudio });
}

export function toggleWaves(on?: boolean) {
  set((s) => ({ waves: on ?? !s.waves }));
}

// ---------------------------------------------------------------------------
// keys (the timeline and the Play all window share them)
// ---------------------------------------------------------------------------

export interface KeyLike {
  key: string;
  ctrlKey: boolean;
  metaKey: boolean;
  shiftKey: boolean;
  altKey: boolean;
  target: EventTarget | null;
  repeat?: boolean;
}

/** Keys in a text field, a CodeMirror editor or anything editable belong to it. */
export function isTyping(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el || typeof el !== "object") return false;
  const tag = el.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  if (el.isContentEditable) return true;
  return typeof el.closest === "function" && !!el.closest(".cm-editor, [contenteditable='true']");
}

/**
 * The cut's shortcuts. Returns true when the key was one (the caller then
 * stops it, so ComfyUI's own Ctrl+Z / space don't also fire).
 */
export function cutKey(e: KeyLike): boolean {
  if (isTyping(e.target)) return false;
  const k = e.key.length === 1 ? e.key.toLowerCase() : e.key;
  const mod = e.ctrlKey || e.metaKey;
  if (mod && !e.altKey && (k === "z" || k === "y")) {
    if (k === "y" || e.shiftKey) void redoCut();
    else void undoCut();
    return true;
  }
  if (e.altKey && !mod && (k === "ArrowLeft" || k === "ArrowRight")) {
    const shot = get().shot;
    if (shot) void nudgeClip(shot, k === "ArrowLeft" ? -1 : 1);
    return true;
  }
  if (mod || e.altKey) return false;
  if (k === " ") {
    const s = get();
    if (s.viewer?.kind === "cut") set({ cutPlay: { ...s.cutPlay, rate: 1 } });
    setCutPlaying(!(s.viewer?.kind === "cut" && s.cutPlay.playing));
    return true;
  }
  if (k === "j" || k === "k" || k === "l") {
    shuttle(k);
    return true;
  }
  if (k === "i" || k === "o") {
    if (!e.repeat) void trimAtPlayhead(k === "i" ? "in" : "out");
    return true;
  }
  // P10: `n` notes what is wrong with the selected clip. Not `i` — that is
  // trim-in at the playhead, which every NLE binds the same way.
  if (k === "n") {
    const shot = get().shot;
    if (shot && !e.repeat) openIssue(shot);
    return !!shot;
  }
  return false;
}

// ---------------------------------------------------------------------------
// waveforms
// ---------------------------------------------------------------------------

export const peaksCache = new PeaksCache((q) => api().peaks(q.ep, q.path, q.bins, q.start, q.end));

/** A pick can't change a locked clip's take: the UI says so before asking. */
export function lockedPickRefusal(shot: string, pass: Pass = get().pass): string | null {
  const s = get();
  if (!s.ep) return null;
  const sh = s.status[statusKey(s.ep, pass)]?.shots.find((x) => x.shot === shot);
  return sh?.cut?.locked ? `${shot} is locked in the ${pass} cut: unlock it (its menu) to change its take` : null;
}
