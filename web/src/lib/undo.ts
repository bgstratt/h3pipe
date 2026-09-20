// Phase 9b: session undo / redo of cut edits. An edit is stored as what it
// changed (the order, and the trims / lock of the shots it touched), not as a
// snapshot of the whole list, so undoing it later doesn't also undo a pick or a
// render that happened since. Undo and redo are each one PUT /h3pipe/cut.

import type { CutEntry } from "../types";
import { sameAudio } from "./audioSource";
import { applyOrder, fieldsOf, withFields, type CutFields } from "./cutEdit";

export interface CutEdit {
  label: string;
  /** the order before and after (null: the edit moved nothing) */
  order: { before: string[]; after: string[] } | null;
  /** the shots whose trims / lock / audio source it changed */
  fields: Record<string, { before: CutFields; after: CutFields }>;
}

const sameFields = (a: CutFields, b: CutFields) =>
  a.trim_in === b.trim_in && a.trim_out === b.trim_out && a.locked === b.locked && sameAudio(a.audio, b.audio);

/** What turned `before` into `after`, or null when nothing changed. */
export function diffEdit(label: string, before: CutEntry[], after: CutEntry[]): CutEdit | null {
  const ob = before.map((e) => e.shot);
  const oa = after.map((e) => e.shot);
  const order = ob.length === oa.length && ob.every((s, i) => s === oa[i]) ? null : { before: ob, after: oa };
  const fields: CutEdit["fields"] = {};
  const prev = new Map(before.map((e) => [e.shot, fieldsOf(e)]));
  for (const e of after) {
    const b = prev.get(e.shot);
    const a = fieldsOf(e);
    if (b && !sameFields(a, b)) fields[e.shot] = { before: b, after: a };
  }
  if (!order && !Object.keys(fields).length) return null;
  return { label, order, fields };
}

/** `current` with the edit undone (or redone): its order, and its shots' fields. */
export function applyEdit(current: CutEntry[], edit: CutEdit, dir: "undo" | "redo"): CutEntry[] {
  let out = edit.order ? applyOrder(current, dir === "undo" ? edit.order.before : edit.order.after) : current.slice();
  for (const [shot, f] of Object.entries(edit.fields)) {
    if (out.some((e) => e.shot === shot)) out = withFields(out, shot, dir === "undo" ? f.before : f.after);
  }
  return out;
}

/** Undo / redo stacks for one cut (an episode's pass). */
export class UndoStack {
  private done: CutEdit[] = [];
  private undone: CutEdit[] = [];
  constructor(private limit = 200) {}

  /** A new edit: it clears what could be redone. */
  push(e: CutEdit) {
    this.done.push(e);
    if (this.done.length > this.limit) this.done.shift();
    this.undone = [];
  }

  /** Take the edit to undo (put it back with `restoreUndo` if the undo fails). */
  takeUndo(): CutEdit | undefined {
    const e = this.done.pop();
    if (e) this.undone.push(e);
    return e;
  }

  takeRedo(): CutEdit | undefined {
    const e = this.undone.pop();
    if (e) this.done.push(e);
    return e;
  }

  /** The undo failed: the edit is still in force. */
  restoreUndo(e: CutEdit) {
    if (this.undone[this.undone.length - 1] === e) this.undone.pop();
    this.done.push(e);
  }

  /** The redo failed: it can still be redone. */
  restoreRedo(e: CutEdit) {
    if (this.done[this.done.length - 1] === e) this.done.pop();
    this.undone.push(e);
  }

  get undoLabel(): string | null {
    return this.done[this.done.length - 1]?.label ?? null;
  }

  get redoLabel(): string | null {
    return this.undone[this.undone.length - 1]?.label ?? null;
  }

  clear() {
    this.done = [];
    this.undone = [];
  }
}
