// The episode status refresh after a change: a refresh asked for while one is
// running runs again when it ends (that one may predate the change), and a pick
// shows at once instead of waiting for the status, without an older status
// putting the old take back.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { pickTake, refreshEpisode } from "../src/actions";
import { setApi, setHost, type Host } from "../src/host";
import { createMockApi } from "../src/mock/mockApi";
import { initialState, statusKey, store } from "../src/store";
import type { EpisodeStatus } from "../src/types";

function deferred<T>() {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((r) => (resolve = r));
  return { promise, resolve };
}

describe("status refresh", () => {
  let api: ReturnType<typeof createMockApi>;
  let EP: string;
  const cur = () => store.get().status[statusKey(EP, "proxy")];

  beforeEach(async () => {
    setHost({ on: () => () => {}, toast: () => {}, show: () => {} } as Host);
    api = createMockApi(() => {}, { latency: 0 });
    setApi(api);
    EP = (await api.episodes())[0].ep;
    store.set(initialState());
    store.set({ ep: EP, pass: "proxy" });
    await refreshEpisode(EP, "proxy");
  });
  afterEach(() => store.set(initialState()));

  /** api.episode, with each call held until the test lets it go. */
  function holdEpisode() {
    const real = api.episode.bind(api);
    const gates: { open: () => void }[] = [];
    const spy = vi.spyOn(api, "episode").mockImplementation(async (ep, pass) => {
      const read = await real(ep, pass);           // read now, as a server would
      const gate = deferred<void>();
      gates.push({ open: () => gate.resolve() });
      await gate.promise;
      return read;
    });
    return { spy, gates };
  }

  it("a refresh asked for during one runs once more after it, shared by every caller", async () => {
    const { spy, gates } = holdEpisode();
    const first = refreshEpisode(EP, "proxy");
    const a = refreshEpisode(EP, "proxy");
    const b = refreshEpisode(EP, "proxy");
    expect(a).toBe(b);
    await vi.waitFor(() => expect(gates).toHaveLength(1));
    gates[0].open();
    await first;
    await vi.waitFor(() => expect(gates).toHaveLength(2));
    expect(spy).toHaveBeenCalledTimes(2);
    gates[1].open();
    await a;
    expect(spy).toHaveBeenCalledTimes(2);
  });

  it("a pick shows before the status, and a status read before it doesn't undo it", async () => {
    const row = cur().shots.find((s) => s.takes.filter((t) => t.status === "ok" && t.has_video).length > 1)!;
    expect(row).toBeTruthy();
    const other = row.takes.find((t) => t.status === "ok" && t.has_video && t.take !== row.cut.take)!;

    const { gates } = holdEpisode();
    const stale = refreshEpisode(EP, "proxy");   // read before the pick
    await vi.waitFor(() => expect(gates).toHaveLength(1));

    await pickTake(row.shot, other.take);
    const shown = () => cur().shots.find((s) => s.shot === row.shot)!.cut;
    expect(shown()).toMatchObject({ take: other.take, picked: true });

    gates[0].open();                              // the old status lands
    await stale;
    expect(shown().take).toBe(other.take);

    await vi.waitFor(() => expect(gates.length).toBeGreaterThan(1));
    for (const g of gates) g.open();
    await vi.waitFor(() => expect(store.get().statusLoading[statusKey(EP, "proxy")]).toBeUndefined());
    const fresh: EpisodeStatus = cur();
    expect(fresh.shots.find((s) => s.shot === row.shot)!.cut).toMatchObject({ take: other.take, picked: true });
  });
});
