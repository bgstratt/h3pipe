// P5 (docs/polish_Plan.md): naming a new episode, and whether the folder it
// would go in is one the editor can write to and find again.
import { describe, expect, it } from "vitest";
import { insideRoot, nameError, suggestName, willBeListed } from "../src/lib/newEpisode";

describe("nameError", () => {
  it("takes the names people actually use", () => {
    for (const n of ["ep01", "ep2", "EP07", "pilot", "s02e04", "ep-01", "ep_1", "ep01.b"]) {
      expect(nameError(n), n).toBeNull();
    }
  });

  it("asks for a name before anything else", () => {
    expect(nameError("")).toMatch(/Give the episode a name/);
    expect(nameError("   ")).toMatch(/Give the episode a name/);
  });

  it("refuses what isn't a folder name", () => {
    for (const n of ["ep 1", "ep/01", "ep\\01", "..", ".hidden", "-ep", "ep:1", "ép01"]) {
      expect(nameError(n), n).toBeTruthy();
    }
    expect(nameError("ep01.")).toMatch(/can't end with a dot/);
  });

  it("refuses names the episode could never be found under again", () => {
    // find_episodes walks past these folders, and Windows keeps the device names
    for (const n of ["refs", "Refs", "audio", "shotlist", "renders", "renders_proxy", "views",
                     "targets", "nul", "COM1", "lpt9"]) {
      expect(nameError(n), n).toMatch(/reserved/);
    }
  });

  it("trims, like the server does", () => {
    expect(nameError("  ep02  ")).toBeNull();
  });
});

describe("suggestName", () => {
  it("offers the next number, keeping the width", () => {
    expect(suggestName([{ name: "ep01" }, { name: "ep02" }])).toBe("ep03");
    expect(suggestName([{ name: "ep9" }])).toBe("ep10");
    expect(suggestName([{ name: "ep008" }])).toBe("ep009");
  });

  it("looks at the highest, not the last listed", () => {
    expect(suggestName([{ name: "ep10" }, { name: "ep02" }, { name: "refs" }])).toBe("ep11");
  });

  it("starts at ep01 when nothing there is numbered", () => {
    expect(suggestName([])).toBe("ep01");
    expect(suggestName([{ name: "pilot" }, { name: "refs" }])).toBe("ep01");
  });
});

describe("where it may go", () => {
  const roots = ["C:\\Shows"];

  it("is inside a root, or the server refuses it", () => {
    expect(insideRoot("C:\\Shows", roots)).toBe(true);
    expect(insideRoot("C:\\Shows\\Porchlights", roots)).toBe(true);
    expect(insideRoot("c:/shows/porchlights/", roots)).toBe(true);   // same folder, typed loosely
    expect(insideRoot("C:\\Elsewhere", roots)).toBe(false);
    expect(insideRoot("C:\\Shows2", roots)).toBe(false);             // not a prefix match
    expect(insideRoot("C:\\Shows", [])).toBe(false);
  });

  it("warns when the episode would sit deeper than a root reaches", () => {
    expect(willBeListed("C:\\Shows", roots)).toBe(true);             // <root>/ep01
    expect(willBeListed("C:\\Shows\\Porchlights", roots)).toBe(true); // <root>/show/ep01
    expect(willBeListed("C:\\Shows\\Porchlights\\season2", roots)).toBe(false);
  });
});
