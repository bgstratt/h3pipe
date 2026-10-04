// A master being assembled: the dialog's words and bar for each step, how the
// titles laid the picture in, and the job the mock keeps (one run at a time,
// still there when the dialog asks again).
import { describe, expect, it } from "vitest";
import { clock, masterFraction, masterStageText, pictureText } from "../src/lib/master";
import { createMockApi } from "../src/mock/mockApi";
import type { MasterJob } from "../src/types";

const JOB: MasterJob = {
  ep: "C:\\Shows\\MyShow\\ep01", pass: "final", state: "running", elsewhere: false, by: "the editor",
  started: "2026-10-04T08:13:32-05:00", finished: null, step: "assemble", stage: "clips",
  done: 212, total: 323, text: "sh2590", output: null, mov: null, report: null, titles: null, error: "",
};

describe("master job in words", () => {
  it("says the step, the count and the shot", () => {
    expect(masterStageText(JOB)).toBe("Writing the clips 212/323 · sh2590");
    expect(masterStageText({ ...JOB, stage: "probe", done: 5 })).toBe("Reading the clips 5/323 · sh2590");
    expect(masterStageText({ ...JOB, stage: "verify", done: null, total: null, text: "x" }))
      .toBe("Counting the master's frames");
    expect(masterStageText({ ...JOB, step: "titles", stage: "titles", text: "outro" })).toBe("Encoding the outro");
    expect(masterStageText({ ...JOB, step: "titles", stage: "reencode", done: 7980, total: 31933 }))
      .toBe("Re-encoding the whole cut with its titles 25%");
  });

  it("names a run it didn't start", () => {
    expect(masterStageText({ ...JOB, elsewhere: true, by: "h3.py master" }))
      .toBe("Being assembled by h3.py master since 08:13");
    expect(masterFraction({ ...JOB, elsewhere: true })).toBeNull();
  });

  it("moves the bar forward through the steps", () => {
    const at = (j: Partial<MasterJob>) => masterFraction({ ...JOB, ...j })!;
    expect(at({ stage: "probe", done: 0 })).toBe(0);
    expect(at({ stage: "clips", done: 0 })).toBeCloseTo(0.1);
    expect(at({ stage: "clips" })).toBeGreaterThan(at({ stage: "clips", done: 100 }));
    expect(at({ stage: "verify", done: null, total: null })).toBeGreaterThan(at({ stage: "clips", done: 323 }));
    expect(at({ step: "titles", stage: "reencode", done: 31933, total: 31933 })).toBe(1);
    expect(masterFraction({ ...JOB, state: "done" })).toBeNull();
  });

  it("says how the picture went in", () => {
    expect(pictureText(null)).toBeNull();
    expect(pictureText({ intro: "a", outro: "b" })).toBeNull();
    expect(pictureText({ intro: "a", outro: "b", picture: "copied" })).toMatch(/copied as assembled/);
    expect(pictureText({ intro: "a", outro: null, picture: "re-encoded", why: "the cut is hevc, not H.264" }))
      .toBe("The whole cut was re-encoded when the titles went on, because the cut is hevc, not H.264.");
    expect(clock("2026-10-04T08:42:48-05:00")).toBe("08:42");
    expect(clock(null)).toBe("");
  });
});

describe("mock master job", () => {
  it("runs once at a time, step by step, and is still there afterwards", async () => {
    const events: [string, unknown][] = [];
    const api = createMockApi((e, d) => events.push([e, d]), { latency: 0 });
    const ep = (await api.episodes())[0].ep;
    expect((await api.masterJob(ep)).job).toBeNull();
    const first = api.master({ ep, pass: "final", action: "assemble", allow_gaps: true });
    // a second press while the first is going is refused, with the job to show
    await expect(api.master({ ep, pass: "final", action: "assemble", allow_gaps: true }))
      .rejects.toMatchObject({ status: 409 });
    const res = await first;
    expect(res.job?.state).toBe("done");
    expect(res.titles?.picture).toBe("copied");
    const seen = events.filter(([e]) => e === "h3pipe.master").map(([, d]) => d as MasterJob);
    expect(seen[0].state).toBe("running");
    expect(seen.some((j) => j.stage === "clips" && j.done === 1)).toBe(true);
    expect(seen.at(-1)!.state).toBe("done");
    const again = (await api.masterJob(ep)).job!;
    expect([again.state, again.output]).toEqual(["done", "master/ep01_master_1920x1080.mp4"]);
  });
});
