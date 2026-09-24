// P8: the Supply flow. The server decides where files go (POST
// /h3pipe/refs/match); these are the rules the editor adds on top -- show the
// table before sending, upload one at a time, and keep a single file on the
// old single-slot path.
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createMockApi } from "../src/mock/mockApi";
import { setApi, setHost } from "../src/host";
import { closeSupply, dropFiles, openSupply, runSupply } from "../src/actions";
import { store } from "../src/store";
import type { Api } from "../src/api";

const file = (name: string) => new File(["x"], name, { type: "image/png" });

async function setup(over: Partial<Api> = {}) {
  const mock = createMockApi(() => {}, { latency: 0 });
  setApi({ ...mock, ...over });
  setHost({ on: () => () => {}, toast: () => {}, show: () => {} });
  const eps = await mock.episodes();
  store.set({ ep: eps[0].ep, supply: null });
  return mock;
}

const settle = () => new Promise((r) => setTimeout(r, 30));

describe("openSupply", () => {
  beforeEach(() => closeSupply());

  it("asks the server where the files go and keeps them for the upload", async () => {
    await setup();
    await openSupply([file("ada_sheet_4panel.png"), file("nothing.png")]);
    const s = store.get().supply!;
    expect(s.files).toHaveLength(2);
    expect(s.match).not.toBeNull();
    expect(s.match!.matched.map((m) => m.ref)).toEqual(["subject:ada"]);
    expect(s.match!.matched[0].view).toBe("sheet");
    expect(s.match!.unmatched.map((u) => u.file)).toEqual(["nothing.png"]);
    expect(s.done).toEqual([]);
  });

  it("does nothing without an episode or without files", async () => {
    await setup();
    store.set({ ep: null });
    await openSupply([file("ada.png")]);
    expect(store.get().supply).toBeNull();
    store.set({ ep: "C:/Shows/ep01" });
    await openSupply([]);
    expect(store.get().supply).toBeNull();
  });

  it("keeps the window open and says so when matching fails", async () => {
    await setup({ refsMatch: () => Promise.reject(new Error("no server")) });
    await openSupply([file("ada.png")]);
    const s = store.get().supply!;
    expect(s.match).toBeNull();
    expect(s.error).toContain("no server");
  });
});

describe("runSupply", () => {
  beforeEach(() => closeSupply());

  it("uploads one file at a time, each picked", async () => {
    let inFlight = 0;
    const order: string[] = [];
    const sent: { ref: string; view: string | null; pick: boolean; name: string }[] = [];
    await setup({
      refsUpload: async (req) => {
        inFlight++;
        expect(inFlight, "two uploads at once").toBe(1);
        order.push(req.name ?? "");
        sent.push({ ref: req.ref, view: req.view ?? null, pick: !!req.pick, name: req.name ?? "" });
        await new Promise((r) => setTimeout(r, 5));
        inFlight--;
        return { take: 1, status: "ok" } as never;
      },
    });
    await openSupply([file("ada_sheet_4panel.png"), file("bo_sheet_4panel.png")]);
    await runSupply();
    expect(order).toEqual(["ada_sheet_4panel.png", "bo_sheet_4panel.png"]);
    expect(sent.every((x) => x.pick)).toBe(true);
    expect(sent.map((x) => x.view)).toEqual(["sheet", "sheet"]);
  });

  it("sends only the matched files", async () => {
    const sent: string[] = [];
    await setup({
      refsUpload: async (req) => {
        sent.push(req.name ?? "");
        return { take: 1, status: "ok" } as never;
      },
    });
    await openSupply([file("ada_sheet_4panel.png"), file("nothing_here.png")]);
    await runSupply();
    expect(sent).toEqual(["ada_sheet_4panel.png"]);
  });

  it("closes itself when everything went in", async () => {
    await setup({ refsUpload: async () => ({ take: 1, status: "ok" }) as never });
    await openSupply([file("ada_sheet_4panel.png")]);
    await runSupply();
    expect(store.get().supply).toBeNull();
  });

  it("stays open with the reason when one fails, and keeps going", async () => {
    await setup({
      refsUpload: async (req) => {
        if (req.name?.startsWith("ada")) throw new Error("413 too big");
        return { take: 1, status: "ok" } as never;
      },
    });
    await openSupply([file("ada_sheet_4panel.png"), file("bo_sheet_4panel.png")]);
    await runSupply();
    const s = store.get().supply!;
    expect(s.done).toHaveLength(2);
    expect(s.done[0]).toMatchObject({ ok: false });
    expect(s.done[0].why).toContain("413");
    expect(s.done[1]).toMatchObject({ ok: true });      // the failure didn't stop it
    expect(s.busy).toBeNull();
  });

  it("does not run twice over the same files", async () => {
    const calls = vi.fn(async () => ({ take: 1, status: "ok" }) as never);
    await setup({ refsUpload: calls });
    await openSupply([file("ada_sheet_4panel.png")]);
    const first = runSupply();
    await runSupply();                                  // while the first is going
    await first;
    await settle();
    expect(calls).toHaveBeenCalledTimes(1);
  });

  it("stops if the window is closed mid-run", async () => {
    const sent: string[] = [];
    await setup({
      refsUpload: async (req) => {
        sent.push(req.name ?? "");
        closeSupply();
        return { take: 1, status: "ok" } as never;
      },
    });
    await openSupply([file("ada_sheet_4panel.png"), file("bo_sheet_4panel.png")]);
    await runSupply();
    expect(sent).toEqual(["ada_sheet_4panel.png"]);
  });
});

describe("dropFiles", () => {
  beforeEach(() => closeSupply());

  it("sends one file straight to the slot it was dropped on", async () => {
    const sent: { ref: string; view: string | null }[] = [];
    await setup({
      refsUpload: async (req) => {
        sent.push({ ref: req.ref, view: req.view ?? null });
        return { take: 1, status: "ok" } as never;
      },
    });
    await dropFiles([file("whatever.png")], { ref: "subject:ada", view: "02_side" });
    expect(sent).toEqual([{ ref: "subject:ada", view: "02_side" }]);
    expect(store.get().supply).toBeNull();          // no table for one file
  });

  it("matches by name once there is more than one, even on a slot", async () => {
    await setup();
    await dropFiles([file("ada_sheet_4panel.png"), file("bo_sheet_4panel.png")],
                    { ref: "subject:ada", view: "02_side" });
    const s = store.get().supply!;
    expect(s.match!.matched.map((m) => m.ref)).toEqual(["subject:ada", "subject:bo"]);
  });

  it("ignores an empty drop", async () => {
    await setup();
    await dropFiles(null);
    await dropFiles([]);
    expect(store.get().supply).toBeNull();
  });
});
