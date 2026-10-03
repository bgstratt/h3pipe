// A clip flagged to come back to: the entry carries `flag`, an edit that flags
// is a change (and undoable), a status carries it back into the entries, and
// Play all still plays the clip.
import { describe, expect, it } from "vitest";
import { applyEntries, entriesOf, sameEntries, withFields } from "../src/lib/cutEdit";
import { applyEdit, diffEdit } from "../src/lib/undo";
import { buildPlaylist } from "../src/lib/playlist";
import { flaggedShots } from "../src/cutActions";
import type { CutEntry, EpisodeStatus, ShotStatus } from "../src/types";

function shot(id: string, extra: Partial<ShotStatus["cut"]> = {}): ShotStatus {
  return {
    shot: id, orphan: false, length: 24, seconds: 1, subjects: [], missing_refs: [],
    override: { fields: [], stale: false },
    cut: { take: 1, picked: false, pass: "proxy", placeholder: false, usable: true, trim_in: 0, trim_out: 0,
           locked: false, note: "", in_cut_file: true, frames: 24, fps: 24, ...extra },
    takes: [{ take: 1, status: "ok", has_video: true, mp4: `renders_proxy/${id}/${id}_t01.mp4`, frames: 24, fps: 24 }],
  } as unknown as ShotStatus;
}

const st = (shots: ShotStatus[]) => ({ ep: "ep", pass: "proxy", fps: 24, shots } as unknown as EpisodeStatus);

describe("flagging a clip", () => {
  it("the entry carries it, and drops it when unflagged", () => {
    const list: CutEntry[] = [{ shot: "a" }, { shot: "b", locked: true }];
    const on = withFields(list, "b", { flag: true });
    expect(on[1]).toEqual({ shot: "b", locked: true, flag: true });
    expect(withFields(on, "b", { flag: false })[1]).toEqual({ shot: "b", locked: true });
  });
  it("is a change, undoable, and survives later edits", () => {
    const s = st([shot("a"), shot("b"), shot("c")]);
    const before = entriesOf(s);
    const after = withFields(before, "b", { flag: true });
    expect(sameEntries(before, after)).toBe(false);
    const edit = diffEdit("Flag b", before, after);
    expect(edit).not.toBeNull();
    expect(applyEdit(after, edit!, "undo")[1]).toEqual({ shot: "b" });
    const flagged = st([shot("a"), shot("b", { flag: true }), shot("c")]);
    expect(entriesOf(flagged)[1]).toEqual({ shot: "b", flag: true });
    expect(withFields(entriesOf(flagged), "c", { trim_in: 2 })[1]).toEqual({ shot: "b", flag: true });
  });
  it("leaving out is undoable too", () => {
    const before = entriesOf(st([shot("a"), shot("b")]));
    expect(diffEdit("Leave b out", before, withFields(before, "b", { out: true }))).not.toBeNull();
  });
  it("the status follows; Play all still plays it; the list is in cut order", () => {
    const s = st([shot("a"), shot("b"), shot("c")]);
    const edited = applyEntries(s, [{ shot: "a", flag: true }, { shot: "b" }, { shot: "c", flag: true }]);
    expect(edited.shots[0].cut.flag).toBe(true);
    expect(buildPlaylist(edited).map((i) => i.shot)).toEqual(["a", "b", "c"]);
    expect(flaggedShots(edited)).toEqual(["a", "c"]);
  });
});
