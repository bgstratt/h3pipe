// One open authored file (the script or the series config) in an editor window:
// what's on disk (text + hash), the buffer, live checking, saving, and noticing
// edits made in another editor. No React and no CodeMirror here, so the conflict
// flow is testable on its own; the window binds it to the editor.
//
//   load()      GET /h3pipe/source
//   edit(text)  the buffer changed: dirty, and a check ~400 ms later
//   poll()      GET …&hash_only=1: changed on disk? clean buffer → reload
//               silently; dirty → a conflict (Reload / Keep mine)
//   save()      PUT /h3pipe/source {base_hash, rebuild}; a 409 → a conflict
//   reload()    take the disk's text (drops the buffer's edits)
//   keepMine()  keep the buffer, now based on the disk's hash (after a failed
//               save: resend with the new hash)

import { ApiError, errText, type Api } from "../api";
import { createStore, type Store } from "../store";
import type { BuildResult, SourceCheck, SourceConflictBody, SourceFile, SourceSaveResult } from "../types";

export const CHECK_DELAY = 400;

export interface SourceConflict {
  /** the disk's hash and text now */
  hash: string;
  text: string;
  /** "disk": noticed while polling; "save": the save was refused (409) */
  reason: "disk" | "save";
}

export interface SavedInfo {
  at: number;
  /** the build ran and both passes built (null: it didn't run) */
  built: boolean | null;
  build: BuildResult | null;
  /** the check's errors at save time (a script with errors is still saved) */
  errors: number;
}

export interface SessionState {
  loading: boolean;
  loadError: string | null;
  /** relative to the episode, as the server names it */
  path: string;
  /** the disk's hash the buffer is based on (null until loaded) */
  baseHash: string | null;
  /** the disk's text the buffer is based on */
  baseText: string;
  /** the buffer, as last told by edit() */
  text: string;
  dirty: boolean;
  /** the last check, and the text it checked */
  check: { text: string; result: SourceCheck } | null;
  checking: boolean;
  checkError: string | null;
  conflict: SourceConflict | null;
  saving: boolean;
  saved: SavedInfo | null;
  saveError: string | null;
}

export interface SessionHooks {
  /** replace the editor's text (a load, a silent reload, Reload) */
  replace?: (text: string, why: "load" | "outside" | "reload") => void;
  /** after a successful save (refresh the episode, show the build) */
  saved?: (r: SourceSaveResult) => void;
  /** setTimeout / clearTimeout (tests) */
  setTimer?: (fn: () => void, ms: number) => unknown;
  clearTimer?: (t: unknown) => void;
}

function initial(): SessionState {
  return {
    loading: false, loadError: null, path: "", baseHash: null, baseText: "", text: "", dirty: false,
    check: null, checking: false, checkError: null, conflict: null, saving: false, saved: null, saveError: null,
  };
}

/** The body of a 409, if `e` is one with the contract's shape. */
export function conflictOf(e: unknown): SourceConflictBody | null {
  if (!(e instanceof ApiError) || e.status !== 409) return null;
  const d = e.data as Partial<SourceConflictBody> | undefined;
  if (!d || typeof d.hash !== "string") return null;
  return { error: d.error ?? "changed on disk", hash: d.hash, text: typeof d.text === "string" ? d.text : "" };
}

export class SourceSession {
  readonly state: Store<SessionState> = createStore(initial());
  private checkTimer: unknown = null;
  private checkSeq = 0;
  private polling = false;
  private disposed = false;

  constructor(
    private api: Api,
    readonly ep: string,
    readonly file: SourceFile,
    private hooks: SessionHooks = {},
  ) {}

  get(): SessionState {
    return this.state.get();
  }

  private set(p: Partial<SessionState>) {
    if (!this.disposed) this.state.set(p);
  }

  private setTimer(fn: () => void, ms: number): unknown {
    return (this.hooks.setTimer ?? ((f, m) => setTimeout(f, m)))(fn, ms);
  }

  private clearTimer(t: unknown) {
    if (t != null) (this.hooks.clearTimer ?? ((x) => clearTimeout(x as ReturnType<typeof setTimeout>)))(t);
  }

  dispose() {
    this.clearTimer(this.checkTimer);
    this.disposed = true;
  }

  /** Take `text` as both the disk's and the buffer's. */
  private adopt(text: string, hash: string, why: "load" | "outside" | "reload") {
    if (this.disposed) return;
    this.set({ baseText: text, baseHash: hash, text, dirty: false, conflict: null });
    this.hooks.replace?.(text, why);
    this.scheduleCheck(0);
  }

  async load(): Promise<void> {
    this.set({ loading: true, loadError: null });
    try {
      const doc = await this.api.source(this.ep, this.file);
      this.set({ loading: false, path: doc.path });
      if (doc.shots?.length) this.set({ check: { text: doc.text, result: { ok: true, errors: [], warnings: [], shots: doc.shots } } });
      this.adopt(doc.text, doc.hash, "load");
    } catch (e) {
      this.set({ loading: false, loadError: errText(e) });
    }
  }

  /** The editor's text changed. */
  edit(text: string) {
    const s = this.get();
    if (text === s.text) return;
    this.set({ text, dirty: text !== s.baseText, saveError: null });
    this.scheduleCheck();
  }

  scheduleCheck(delay = CHECK_DELAY) {
    this.clearTimer(this.checkTimer);
    this.checkTimer = this.setTimer(() => {
      this.checkTimer = null;
      void this.runCheck();
    }, delay);
  }

  /** POST /h3pipe/source/check of the buffer; an answer for older text is dropped. */
  async runCheck(): Promise<void> {
    const text = this.get().text;
    const seq = ++this.checkSeq;
    this.set({ checking: true });
    try {
      const result = await this.api.checkSource(this.ep, this.file, text);
      if (seq !== this.checkSeq) return;
      this.set({ check: { text, result }, checking: false, checkError: null });
    } catch (e) {
      if (seq !== this.checkSeq) return;
      this.set({ checking: false, checkError: errText(e) });
    }
  }

  /** Has the file changed on disk? See the header. Returns what happened. */
  async poll(): Promise<"same" | "reloaded" | "conflict" | "skipped"> {
    const s = this.get();
    if (this.polling || s.loading || s.saving || s.baseHash == null) return "skipped";
    this.polling = true;
    try {
      const h = await this.api.sourceHash(this.ep, this.file);
      const now = this.get();
      if (h.hash === now.baseHash || h.hash === now.conflict?.hash) return "same";
      const doc = await this.api.source(this.ep, this.file);
      if (doc.hash === this.get().baseHash) return "same";
      if (!this.get().dirty) {
        this.adopt(doc.text, doc.hash, "outside");
        return "reloaded";
      }
      this.set({ conflict: { hash: doc.hash, text: doc.text, reason: "disk" } });
      return "conflict";
    } catch {
      return "skipped"; // polling is best-effort; a real error shows on save
    } finally {
      this.polling = false;
    }
  }

  async save(): Promise<SourceSaveResult | null> {
    const s = this.get();
    if (s.saving || s.baseHash == null) return null;
    const text = s.text;
    this.set({ saving: true, saveError: null });
    try {
      const r = await this.api.putSource({ ep: this.ep, file: this.file, text, base_hash: s.baseHash, rebuild: true });
      const now = this.get();
      const built = r.build ? !!r.build.ok : null;
      this.set({
        saving: false, baseHash: r.hash, baseText: text, dirty: now.text !== text, conflict: null,
        check: r.check ? { text, result: r.check } : now.check,
        saved: { at: Date.now(), built, build: r.build ?? null, errors: r.check?.errors?.length ?? 0 },
      });
      this.hooks.saved?.(r); // (even if the window closed meanwhile: the episode still changed)
      return r;
    } catch (e) {
      const c = conflictOf(e);
      if (c) this.set({ saving: false, conflict: { hash: c.hash, text: c.text, reason: "save" } });
      else this.set({ saving: false, saveError: errText(e) });
      return null;
    }
  }

  /** Drop the buffer's edits for the disk's text. */
  reload() {
    const c = this.get().conflict;
    if (c) this.adopt(c.text, c.hash, "reload");
    else void this.load();
  }

  /** Keep the buffer; it's now based on the disk's current version, so the next
   * save overwrites it. After a refused save, save again. */
  async keepMine(): Promise<void> {
    const c = this.get().conflict;
    if (!c) return;
    this.set({ baseHash: c.hash, baseText: c.text, dirty: this.get().text !== c.text, conflict: null });
    if (c.reason === "save") await this.save();
  }

  /** Forget the last save's result note. */
  dismissSaved() {
    this.set({ saved: null, saveError: null });
  }
}
