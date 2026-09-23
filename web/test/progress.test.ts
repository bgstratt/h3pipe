// P1 (docs/polish_Plan.md): a pass's progress, rate and estimate, from take
// sidecars. The rate is wall-clock throughput, so a run's queue waiting counts.
import { describe, expect, it } from "vitest";
import {
  fmtDuration, missingShots, passProgress, progressLine, progressTitle, RUN_GAP_S, staleReasons,
  staleShots,
} from "../src/lib/progress";
import type { ShotStatus, TakeSummary } from "../src/types";

const T0 = Date.parse("2026-09-22T10:00:00-05:00");
const iso = (offset_s: number) => new Date(T0 + offset_s * 1000).toISOString();

function take(n: number, status: TakeSummary["status"], queued_s?: number, finished_s?: number): TakeSummary {
  return {
    take: n, status, has_video: status === "ok", seed: "1", seed_source: "stable", note: "",
    overrides: [], stale: [], thumb: null, strip: null, mp4: status === "ok" ? "a.mp4" : null,
    queued: queued_s == null ? null : iso(queued_s),
    finished: finished_s == null ? null : iso(finished_s),
    save_notes: "",
  };
}

function shot(id: string, takes: TakeSummary[] = [], orphan = false): ShotStatus {
  return {
    shot: id, orphan, sequence: "sq01", length: 73, seconds: 3, size: "medium", subjects: [],
    audio_policy: "generate", takes,
    cut: { take: null, picked: false, pass: "proxy", placeholder: false, usable: false,
           trim_in: 0, trim_out: 0, locked: false, note: "", in_cut_file: false },
    override: { fields: [], stale: false },
  } as ShotStatus;
}

describe("passProgress counts", () => {
  it("is empty for an episode with no takes", () => {
    const p = passProgress([shot("sh010"), shot("sh020")]);
    expect(p).toMatchObject({ done: 0, total: 2, queued: 0, failed: 0, todo: 2 });
    expect(p.per_shot_s).toBeNull();
    expect(p.remaining_s).toBeNull();
    expect(progressLine(p)).toBe("0/2");
    expect(progressTitle(p)).toContain("no rate yet");
  });

  it("leaves orphans out of the total", () => {
    expect(passProgress([shot("sh010"), shot("orph", [], true)]).total).toBe(1);
  });

  it("counts done, queued, failed and not-yet-asked-for separately", () => {
    const p = passProgress([
      shot("a", [take(1, "ok", 0, 20)]),
      shot("b", [take(1, "queued", 30)]),
      shot("c", [take(1, "ok", 0, 10), take(2, "failed")]),   // newest failed, but it has a take
      shot("d", [take(1, "failed")]),
      shot("e"),
    ]);
    expect(p).toMatchObject({ done: 2, total: 5, queued: 1, failed: 1, todo: 1 });
  });
});

describe("the rate is wall clock, not take duration", () => {
  // three takes queued together and finished 20 s apart: each take's own
  // queued -> finished grows (queue waiting), but the pass does 20 s a shot
  const batch = [
    shot("a", [take(1, "ok", 0, 20)]),
    shot("b", [take(1, "ok", 0, 40)]),
    shot("c", [take(1, "ok", 0, 60)]),
    shot("d", [take(1, "queued", 0)]),
    shot("e"),
  ];

  it("divides the run's wall clock by its finished takes", () => {
    const p = passProgress(batch);
    expect(p.run_takes).toBe(3);
    expect(p.elapsed_s).toBe(60);            // first queue -> last finish
    expect(p.per_shot_s).toBe(20);           // not the 40 s mean of the take durations
    expect(p.remaining_s).toBe(40);          // two shots left
    expect(p.running).toBe(true);
    expect(progressLine(p)).toBe("3/5 · 1q · 60s · ~20s/shot · ~40s left");
  });

  it("needs two finished takes before it claims a rate", () => {
    const p = passProgress([shot("a", [take(1, "ok", 0, 20)]), shot("b")]);
    expect(p.run_takes).toBe(1);
    expect(p.per_shot_s).toBeNull();
    expect(progressLine(p)).toBe("1/2");
  });

  it("gives no estimate when nothing is queued, but keeps the rate", () => {
    const p = passProgress(batch.slice(0, 3));
    expect(p.per_shot_s).toBe(20);
    expect(p.running).toBe(false);
    expect(progressLine(p)).not.toContain("left");
    expect(progressTitle(p)).toContain("nothing queued");
  });

  it("stops at a gap, so an older run doesn't drag the rate", () => {
    const p = passProgress([
      shot("old1", [take(1, "ok", -RUN_GAP_S * 3, -RUN_GAP_S * 3 + 10)]),
      shot("old2", [take(1, "ok", -RUN_GAP_S * 3, -RUN_GAP_S * 3 + 20)]),
      shot("new1", [take(1, "ok", 0, 20)]),
      shot("new2", [take(1, "ok", 0, 40)]),
    ]);
    expect(p.run_takes).toBe(2);             // only today's
    expect(p.per_shot_s).toBe(20);
  });

  it("ignores takes with no timestamps (from before sidecars)", () => {
    const p = passProgress([shot("a", [take(1, "ok")]), shot("b", [take(1, "ok")])]);
    expect(p.done).toBe(2);
    expect(p.per_shot_s).toBeNull();
  });

  it("is 0 remaining when every shot is done", () => {
    const p = passProgress(batch.slice(0, 3).concat());
    expect(p.remaining_s).toBe(0);
  });
});

describe("fmtDuration", () => {
  it("reads at a glance", () => {
    expect(fmtDuration(0)).toBe("0s");
    expect(fmtDuration(45)).toBe("45s");
    expect(fmtDuration(89)).toBe("89s");
    expect(fmtDuration(90)).toBe("2m");
    expect(fmtDuration(24 * 60)).toBe("24m");
    expect(fmtDuration(68 * 60)).toBe("1h 8m");
    expect(fmtDuration(null)).toBe("");
  });
});


describe("what the pass still needs (P2)", () => {
  const stale = (n: number, reasons: string[]) => ({ ...take(n, "ok", 0, 10), stale: reasons });

  it("missingShots is shots with no take and nothing queued", () => {
    expect(missingShots([
      shot("a", [take(1, "ok", 0, 10)]),
      shot("b"),
      shot("c", [take(1, "queued", 0)]),
      shot("d", [take(1, "failed")]),
      shot("orph", [], true),
    ])).toEqual(["b", "d"]);
  });

  it("staleShots is the newest finished take being out of date", () => {
    expect(staleShots([
      shot("script", [stale(1, ["script"])]),
      shot("ref", [stale(1, ["ref"])]),
      shot("preset", [stale(1, ["preset"])]),
      shot("target", [stale(1, ["target"])]),
      shot("fine", [take(1, "ok", 0, 10)]),
      shot("unknown-only", [stale(1, ["unknown"])]),       // no provenance: prove nothing
      shot("orph", [stale(1, ["script"])], true),
    ])).toEqual(["script", "ref", "preset", "target"]);
  });

  it("judges the newest take, not an older one", () => {
    expect(staleShots([shot("a", [stale(1, ["script"]), take(2, "ok", 0, 20)])])).toEqual([]);
    expect(staleShots([shot("b", [take(1, "ok", 0, 10), stale(2, ["script"])])])).toEqual(["b"]);
  });

  it("leaves a shot alone while a take of it is queued", () => {
    expect(staleShots([shot("a", [stale(1, ["script"]), take(2, "queued", 0)])])).toEqual([]);
  });

  it("counts the reasons for the button's tooltip", () => {
    expect(staleReasons([
      shot("a", [stale(1, ["script"])]),
      shot("b", [stale(1, ["script", "ref"])]),
      shot("c", [stale(1, ["unknown"])]),
    ])).toEqual({ script: 2, ref: 1 });
  });
});


describe("Re-render stale queues at the built seed", () => {
  it("keeps seed_mode 'same' whether it queues straight away or asks first", async () => {
    const { createMockApi } = await import("../src/mock/mockApi");
    const { setApi, setHost } = await import("../src/host");
    const { renderShots, renderStale } = await import("../src/actions");
    const { store } = await import("../src/store");

    const mock = createMockApi(() => {}, { latency: 0 });
    const sent: Record<string, unknown>[] = [];
    setApi({ ...mock, render: async (r) => { sent.push(r as never); return { queued: [], skipped: [], errors: [] }; } });
    setHost({ on: () => () => {}, toast: () => {}, show: () => {} });

    const eps = await mock.episodes();
    const ep = eps[0].ep;
    const st = await mock.episode(ep, "proxy");
    const target = st.shots.find((x) => x.takes.some((t) => t.status === "ok"))!;
    target.takes[target.takes.length - 1].stale = ["script"];
    store.set({ ep, pass: "proxy", status: { [`${ep}|proxy`]: st }, renderAsk: null });

    renderStale();
    await new Promise((r) => setTimeout(r, 20));

    const ask = store.get().renderAsk;
    if (ask) {
      // it needed a look first (missing refs / a target that isn't ready): the
      // seed mode has to survive the detour, or the dialog would roll new seeds
      expect(ask).toMatchObject({ redo: true, seedMode: "same", shots: [target.shot] });
      await renderShots(ask.shots, ask.redo, true, null, ask.seedMode ?? "auto");
    }
    expect(sent).toHaveLength(1);
    expect(sent[0]).toMatchObject({
      pass: "proxy", redo: true, seed_mode: "same", shots: [target.shot],
    });
  });
});
