import { describe, expect, it } from "vitest";
import { applySide, diffStats, diffWords, tokenize } from "../src/lib/diff";

describe("diffWords", () => {
  it("finds an inserted and a deleted word", () => {
    const d = diffWords("The camera slowly pushes in.", "The camera pushes in fast.");
    expect(d.filter((p) => p.op === "del").map((p) => p.text.trim())).toContain("slowly");
    expect(d.filter((p) => p.op === "add").map((p) => p.text.trim()).join(" ")).toMatch(/fast/);
    expect(diffStats(d)).toEqual({ added: 2, removed: 2 }); // "in." -> "in fast."
  });

  it("rebuilds both sides exactly", () => {
    const a = "summary:\n[ref] Bolt waves.\n\nretention_analysis:\n<Subject 1> kept.";
    const b = "summary:\n[ref] Bolt waves twice, happily.\n\nretention_analysis:\n<Subject 1> kept.\nextra";
    const d = diffWords(a, b);
    expect(applySide(d, "old")).toBe(a);
    expect(applySide(d, "new")).toBe(b);
  });

  it("is all-equal for identical text, and handles empty sides", () => {
    expect(diffWords("same text", "same text")).toEqual([{ op: "eq", text: "same text" }]);
    expect(diffWords("", "new words")).toEqual([{ op: "add", text: "new words" }]);
    expect(diffWords("old words", "")).toEqual([{ op: "del", text: "old words" }]);
  });

  it("merges runs of the same op", () => {
    const d = diffWords("a b c", "x y z");
    for (let i = 1; i < d.length; i++) expect(d[i].op).not.toBe(d[i - 1].op);
  });

  it("stays fast and correct on huge inputs (line fallback)", () => {
    const line = (i: number) => `line ${i} with some words in it\n`;
    const a = Array.from({ length: 3000 }, (_, i) => line(i)).join("");
    const b = a.replace(line(1500), "a changed line\n");
    const t = performance.now();
    const d = diffWords(a, b);
    expect(performance.now() - t).toBeLessThan(3000);
    expect(applySide(d, "old")).toBe(a);
    expect(applySide(d, "new")).toBe(b);
  });

  it("tokenizes words and whitespace separately", () => {
    expect(tokenize("a  b\nc")).toEqual(["a", "  ", "b", "\n", "c"]);
  });
});
