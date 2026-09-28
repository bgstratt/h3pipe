// A shot left out of the cut: the entry carries `out`, Play all skips it, the
// status follows at once, and its neighbours skip it (keyframes from the previous shot).
import { describe, expect, it } from "vitest";
import { applyEntries, withFields } from "../src/lib/cutEdit";
import { buildPlaylist } from "../src/lib/playlist";
import { cutNeighbour } from "../src/lib/keyframes";
import { shotBadges } from "../src/lib/format";
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

describe("leaving a shot out of the cut", () => {
  it("the entry carries it, and drops it when put back", () => {
    const list: CutEntry[] = [{ shot: "a" }, { shot: "b", trim_in: 2 }];
    const out = withFields(list, "b", { out: true });
    expect(out[1]).toEqual({ shot: "b", trim_in: 2, out: true });
    expect(withFields(out, "b", { out: false })[1]).toEqual({ shot: "b", trim_in: 2 });
  });
  it("Play all skips it; the status follows the edit", () => {
    const s = st([shot("a"), shot("b"), shot("c")]);
    const edited = applyEntries(s, [{ shot: "a" }, { shot: "b", out: true }, { shot: "c" }]);
    expect(edited.shots[1].cut.out).toBe(true);
    const items = buildPlaylist(edited);
    expect(items.map((i) => i.shot)).toEqual(["a", "c"]);
    expect(items[1].start).toBeCloseTo(1);
  });
  it("neighbours skip it; it has a badge", () => {
    const s = st([shot("a"), shot("b", { out: true }), shot("c")]);
    expect(cutNeighbour(s, "c", -1)).toBe("a");
    expect(cutNeighbour(s, "b", -1)).toBe("a");
    expect(shotBadges(s.shots[1]).map((b) => b.kind)).toContain("out");
  });
});
