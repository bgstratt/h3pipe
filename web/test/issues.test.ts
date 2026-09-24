// P10c: the editor's side of the issue notepad — the note box, the list, and the
// `n` key. The rules (what a note snapshots, when it reads addressed) are the
// server's, tested in tests/test_issues.py; these are the editor's own.
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createMockApi } from "../src/mock/mockApi";
import { setApi, setHost } from "../src/host";
import {
  clearIssues, closeIssue, issuesOf, loadIssues, openIssue, resolveIssue, saveIssue,
  setIssueText,
} from "../src/actions";
import { cutKey } from "../src/cutActions";
import { store } from "../src/store";
import type { Api } from "../src/api";

async function setup(over: Partial<Api> = {}) {
  const mock = createMockApi(() => {}, { latency: 0 });
  setApi({ ...mock, ...over });
  setHost({ on: () => () => {}, toast: () => {}, show: () => {} });
  const eps = await mock.episodes();
  const ep = eps[0].ep;
  const st = await mock.episode(ep, "proxy");
  store.set({ ep, pass: "proxy", status: { [`${ep}|proxy`]: st }, issues: {}, issueDraft: null });
  return { mock, ep, shot: st.shots[0].shot };
}

// these run without a DOM, so the event's target is the little that isTyping
// looks at: a tag name, and no closest()
const key = (k: string, over: Record<string, unknown> = {}) => ({
  key: k, target: { tagName: "DIV" }, ctrlKey: false, metaKey: false,
  altKey: false, shiftKey: false, repeat: false, ...over,
}) as never;

describe("the note box", () => {
  beforeEach(() => closeIssue());

  it("opens on the shot with the take the cut plays", async () => {
    const { ep, shot } = await setup();
    const cutTake = store.get().status[`${ep}|proxy`].shots
      .find((s) => s.shot === shot)!.cut.take;
    openIssue(shot);
    expect(store.get().issueDraft).toMatchObject({ shot, pass: "proxy", take: cutTake, text: "" });
  });

  it("takes a named take over the cut's", async () => {
    const { shot } = await setup();
    openIssue(shot, "proxy", 7);
    expect(store.get().issueDraft!.take).toBe(7);
  });

  it("closes the context menu when it opens", async () => {
    const { shot } = await setup();
    store.set({ menu: { x: 1, y: 1, shot, pass: "proxy", take: null } });
    openIssue(shot);
    expect(store.get().menu).toBeNull();
  });

  it("refuses to save an empty note, without asking the server", async () => {
    const calls = vi.fn();
    const { shot } = await setup({ addIssue: calls as never });
    openIssue(shot);
    setIssueText("   ");
    expect(await saveIssue()).toBe(false);
    expect(calls).not.toHaveBeenCalled();
    expect(store.get().issueDraft!.error).toMatch(/what is wrong/i);
    expect(store.get().issueDraft).not.toBeNull();       // the box stays open
  });

  it("saves, closes and shows up in the list", async () => {
    const { ep, shot } = await setup();
    openIssue(shot);
    setIssueText("  Kell enters from the wrong side  ");
    expect(await saveIssue()).toBe(true);
    expect(store.get().issueDraft).toBeNull();
    const items = issuesOf(ep, "proxy");
    expect(items).toHaveLength(1);
    expect(items[0].note).toBe("Kell enters from the wrong side");
    expect(items[0].shot).toBe(shot);
  });

  it("keeps the box open with the reason when the server refuses", async () => {
    const { shot } = await setup({
      addIssue: () => Promise.reject(new Error("sh999 is not in the proxy shotlist")),
    });
    openIssue(shot);
    setIssueText("something");
    expect(await saveIssue()).toBe(false);
    const d = store.get().issueDraft!;
    expect(d.error).toContain("not in the proxy shotlist");
    expect(d.text).toBe("something");                    // the typing isn't lost
    expect(d.busy).toBe(false);
  });

  it("does not save twice while the first is in flight", async () => {
    let n = 0;
    const { shot } = await setup({
      addIssue: async (req) => {
        n++;
        await new Promise((r) => setTimeout(r, 10));
        return { id: "x", shot: req.shot, pass: req.pass, take: null, note: req.note,
                 when: "now" } as never;
      },
    });
    openIssue(shot);
    setIssueText("once");
    const first = saveIssue();
    await saveIssue();
    await first;
    expect(n).toBe(1);
  });
});

describe("the n key", () => {
  it("opens the note box for the selected clip", async () => {
    const { shot } = await setup();
    store.set({ shot, issueDraft: null });
    expect(cutKey(key("n"))).toBe(true);
    expect(store.get().issueDraft).toMatchObject({ shot });
  });

  it("does nothing with no clip selected", async () => {
    await setup();
    store.set({ shot: null, issueDraft: null });
    expect(cutKey(key("n"))).toBe(false);
    expect(store.get().issueDraft).toBeNull();
  });

  it("leaves i and o as the trims they have always been", async () => {
    const { shot } = await setup();
    store.set({ shot, issueDraft: null });
    cutKey(key("i"));
    cutKey(key("o"));
    expect(store.get().issueDraft).toBeNull();
  });

  it("ignores a held key and anything typed in a field", async () => {
    const { shot } = await setup();
    store.set({ shot, issueDraft: null });
    cutKey(key("n", { repeat: true }));
    expect(store.get().issueDraft).toBeNull();
    expect(cutKey(key("n", { target: { tagName: "TEXTAREA" } }))).toBe(false);
    expect(store.get().issueDraft).toBeNull();
  });

  it("is not taken by a modifier chord", async () => {
    const { shot } = await setup();
    store.set({ shot, issueDraft: null });
    expect(cutKey(key("n", { ctrlKey: true }))).toBe(false);
    expect(store.get().issueDraft).toBeNull();
  });
});

describe("the list", () => {
  it("resolves one and leaves the rest", async () => {
    const { ep, shot } = await setup();
    const st = store.get().status[`${ep}|proxy`];
    const other = st.shots[1].shot;
    openIssue(shot);
    setIssueText("a");
    await saveIssue();
    openIssue(other);
    setIssueText("b");
    await saveIssue();
    const first = issuesOf(ep, "proxy")[0];
    await resolveIssue(first.id);
    expect(issuesOf(ep, "proxy").map((x) => x.note)).toEqual(["b"]);
  });

  it("clears the pass when confirmed, and not when refused", async () => {
    const { ep, shot } = await setup();
    openIssue(shot);
    setIssueText("a");
    await saveIssue();
    const confirmSpy = vi.fn().mockReturnValue(false);
    vi.stubGlobal("confirm", confirmSpy);

    await clearIssues(false);
    expect(issuesOf(ep, "proxy")).toHaveLength(1);

    confirmSpy.mockReturnValue(true);
    await clearIssues(false);
    expect(issuesOf(ep, "proxy")).toHaveLength(0);
    vi.unstubAllGlobals();
  });

  it("does not ask when there is nothing to clear", async () => {
    const { ep } = await setup();
    const confirmSpy = vi.fn().mockReturnValue(true);
    vi.stubGlobal("confirm", confirmSpy);
    await clearIssues(false);
    expect(confirmSpy).not.toHaveBeenCalled();
    expect(issuesOf(ep, "proxy")).toHaveLength(0);
    vi.unstubAllGlobals();
  });

  it("survives a server with no issue routes", async () => {
    const { ep } = await setup({ issues: () => Promise.reject(new Error("404")) });
    await loadIssues(ep, "proxy");
    expect(issuesOf(ep, "proxy")).toEqual([]);           // no notepad, no noise
  });
});
