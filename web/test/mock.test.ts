// The mock is what the dev page runs on: check it serves the real episode's
// shapes (seeds as strings) and that a simulated render goes queued -> ok.
import { describe, expect, it, vi } from "vitest";
import { createMockApi } from "../src/mock/mockApi";

describe("mock API", () => {
  it("serves the smoke episode with string seeds", async () => {
    const api = createMockApi(() => {}, { latency: 0 });
    const [ep] = await api.episodes();
    expect(ep.name).toBe("ep05");
    const st = await api.episode(ep.ep, "proxy");
    const seeds = st.shots.flatMap((s) => s.takes.map((t) => t.seed)).filter((s) => s != null);
    expect(seeds.length).toBeGreaterThan(0);
    for (const s of seeds) expect(typeof s).toBe("string");
    expect(seeds).toContain("6430499148929255544");
    const d = await api.shot(ep.ep, "proxy", "sh030");
    expect(typeof d.effective.seed).toBe("string");
    expect(d.override.prompt).toBeTruthy();
  });

  it("simulates a render and emits the live events", async () => {
    vi.useFakeTimers();
    try {
      const events: string[] = [];
      const api = createMockApi((e) => events.push(e), { latency: 0 });
      const ep = (await api.episodes())[0].ep;
      const r = await api.render({
        ep, pass: "proxy", shots: ["sh050"], redo: false, seed_mode: "auto", seed: null, model: null, loras: null,
        steps: null, prompt: null, parent_take: null, note: "",
      });
      expect(r.queued).toHaveLength(1);
      expect((await api.episode(ep, "proxy")).shots.find((s) => s.shot === "sh050")!.takes[0].status).toBe("queued");
      await vi.runAllTimersAsync();
      const s = (await api.episode(ep, "proxy")).shots.find((x) => x.shot === "sh050")!;
      expect(s.takes[0].status).toBe("ok");
      expect(s.cut.take).toBe(1);
      expect(events).toEqual(expect.arrayContaining(["h3pipe.take", "execution_start", "progress", "execution_success"]));
    } finally {
      vi.useRealTimers();
    }
  });

  it("pick refuses an unusable take with 409", async () => {
    const api = createMockApi(() => {}, { latency: 0 });
    const ep = (await api.episodes())[0].ep;
    await expect(api.pick({ ep, pass: "proxy", shot: "sh040", take: 1, from_pass: null })).rejects.toMatchObject({ status: 409 });
  });
});
