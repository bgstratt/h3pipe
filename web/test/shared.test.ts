// P9: warning before a pick or a clear changes a live file other episodes read.
// In a real show almost every ref is shared (110 of 110 in Porchlights ep01), so
// the rule has to be about WHOSE file is live, not about sharing itself --
// otherwise it warns on every pick and gets clicked through.
import { describe, expect, it } from "vitest";
import { ownsLive, sharedNote, sharedWarning } from "../src/lib/shared";

const EP = "C:\\Shows\\Porchlights\\ep01";

const ref = (over: Record<string, unknown> = {}) => ({
  name: "Walker",
  exists: true,
  shared_with: ["ep02", "ep03"],
  live_owner: "ep01",
  ...over,
}) as never;

describe("nothing to say", () => {
  it("stays quiet when nothing else reads the file", () => {
    expect(sharedWarning(ref({ shared_with: [] }), EP, "pick")).toBeNull();
    expect(sharedWarning(ref({ shared_with: undefined }), EP, "pick")).toBeNull();
    expect(sharedWarning(ref({ shared_with: [] }), EP, "clear")).toBeNull();
  });

  it("stays quiet when the live file is this episode's own pick", () => {
    expect(sharedWarning(ref(), EP, "pick")).toBeNull();
    expect(sharedNote(ref(), EP)).toBeNull();
  });

  it("stays quiet picking where there is no live file yet", () => {
    expect(sharedWarning(ref({ exists: false, live_owner: null }), EP, "pick")).toBeNull();
  });
});

describe("picking over someone else's file", () => {
  it("names the owner and says it can be got back", () => {
    const w = sharedWarning(ref({ live_owner: "ep03" }), EP, "pick")!;
    expect(w.gone).toBe(false);
    expect(w.title).toContain("ep02 and ep03");
    expect(w.body).toContain("ep03 picked the file");
    expect(w.body).toContain("ep03 can re-pick");
    expect(w.body).toContain("stale: ref");
  });

  it("is loud when nothing has a candidate for the file", () => {
    const w = sharedWarning(ref({ live_owner: null }), EP, "pick")!;
    expect(w.gone).toBe(true);
    expect(w.title).toContain("no candidate behind it");
    expect(w.body).toContain("outside the editor");
    expect(w.body).toContain("for good");
  });
});

describe("clearing or discarding the live take", () => {
  it("warns even when the file is this episode's own, because it deletes", () => {
    const w = sharedWarning(ref(), EP, "clear")!;
    expect(w.title).toContain("Delete");
    expect(w.body).toContain("blocked");
    expect(w.body).toContain("ep01 can re-pick");
  });

  it("says nothing can bring back a hand-placed file", () => {
    const w = sharedWarning(ref({ live_owner: null }), EP, "clear")!;
    expect(w.gone).toBe(true);
    expect(w.body).toContain("nothing has a copy");
  });
});

describe("how the episodes are listed", () => {
  const eps = (n: number) => Array.from({ length: n }, (_, i) => `ep${String(i + 2).padStart(2, "0")}`);

  it("reads as a sentence up to four, then counts", () => {
    const say = (n: number) =>
      sharedWarning(ref({ shared_with: eps(n), live_owner: null }), EP, "clear")!.title;
    expect(say(1)).toContain("ep02 read");
    expect(say(2)).toContain("ep02 and ep03");
    expect(say(3)).toContain("ep02, ep03 and ep04");
    expect(say(9)).toContain("ep02, ep03, ep04 and 6 more");
  });
});

describe("ownsLive", () => {
  it("compares the episode's folder name, not its path", () => {
    expect(ownsLive({ live_owner: "ep01" }, EP)).toBe(true);
    expect(ownsLive({ live_owner: "ep01" }, "C:/Other/Show/ep01/")).toBe(true);
    expect(ownsLive({ live_owner: "ep02" }, EP)).toBe(false);
    expect(ownsLive({ live_owner: null }, EP)).toBe(false);
    expect(ownsLive({ live_owner: "ep01" }, null)).toBe(false);
  });
});

describe("the row badge", () => {
  it("says which episode's pick is live", () => {
    expect(sharedNote(ref({ live_owner: "ep03" }), EP)).toMatchObject({ label: "from ep03" });
  });

  it("flags a file this tool didn't write", () => {
    const n = sharedNote(ref({ live_owner: null }), EP)!;
    expect(n.label).toBe("not from here");
    expect(n.title).toContain("no candidate behind it");
  });

  it("says nothing without a file, or when nothing is shared", () => {
    expect(sharedNote(ref({ exists: false, live_owner: null }), EP)).toBeNull();
    expect(sharedNote(ref({ shared_with: [], live_owner: null }), EP)).toBeNull();
  });
});
