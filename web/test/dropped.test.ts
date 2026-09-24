// P8: the files in a drop, including inside a dropped folder. A browser hands a
// folder over as an entry, not a File, so `dataTransfer.files` alone ignores it.
import { describe, expect, it } from "vitest";
import { DROP_LIMIT, filesFromDrag, type DropEntry } from "../src/lib/dropped";

const file = (name: string) => new File(["x"], name, { type: "image/png" });

function fileEntry(name: string): DropEntry {
  return { isFile: true, name, file: (ok) => ok(file(name)) };
}

function dirEntry(name: string, kids: DropEntry[]): DropEntry {
  return {
    isDirectory: true,
    name,
    createReader: () => {
      let sent = false;
      // readEntries pages, and an empty page means the end. The flag is set
      // BEFORE calling back, because the reader may be asked again from inside
      // that callback.
      return {
        readEntries: (ok) => {
          const batch = sent ? [] : kids;
          sent = true;
          ok(batch);
        },
      };
    },
  };
}

const drag = (entries: (DropEntry | null)[], files: File[] = []) => ({
  items: entries.map((e) => ({ webkitGetAsEntry: () => e })),
  files,
});

const names = (fs: File[]) => fs.map((f) => f.name);

describe("filesFromDrag", () => {
  it("takes plain files", async () => {
    const got = await filesFromDrag(drag([fileEntry("a.png"), fileEntry("b.png")]));
    expect(names(got)).toEqual(["a.png", "b.png"]);
  });

  it("looks inside a dropped folder", async () => {
    const got = await filesFromDrag(drag([
      dirEntry("refs", [fileEntry("ada_sheet.png"), fileEntry("porch.png")]),
    ]));
    expect(names(got)).toEqual(["ada_sheet.png", "porch.png"]);
  });

  it("goes two levels deep and no further", async () => {
    const deep = dirEntry("show", [
      fileEntry("top.png"),
      dirEntry("refs", [fileEntry("mid.png"), dirEntry("more", [fileEntry("too_deep.png")])]),
    ]);
    expect(names(await filesFromDrag(drag([deep])))).toEqual(["top.png", "mid.png"]);
  });

  it("mixes files and folders in one drop", async () => {
    const got = await filesFromDrag(drag([
      fileEntry("loose.png"),
      dirEntry("refs", [fileEntry("inside.png")]),
    ]));
    expect(names(got)).toEqual(["loose.png", "inside.png"]);
  });

  it("falls back to `files` when the entry API isn't there", async () => {
    expect(names(await filesFromDrag({ files: [file("a.png")] }))).toEqual(["a.png"]);
    // entries present but unreadable: keep what `files` had rather than nothing
    const got = await filesFromDrag(drag([{ isFile: true, name: "x.png" }], [file("x.png")]));
    expect(names(got)).toEqual(["x.png"]);
  });

  it("survives a browser that throws at webkitGetAsEntry", async () => {
    const got = await filesFromDrag({
      items: [{ webkitGetAsEntry: () => { throw new Error("nope"); } }],
      files: [file("a.png")],
    });
    expect(names(got)).toEqual(["a.png"]);
  });

  it("survives an entry whose file() errors", async () => {
    const bad: DropEntry = { isFile: true, name: "bad.png", file: (_ok, err) => err?.(new Error("gone")) };
    expect(names(await filesFromDrag(drag([bad, fileEntry("good.png")])))).toEqual(["good.png"]);
  });

  it("is empty for a drop with nothing in it", async () => {
    expect(await filesFromDrag(null)).toEqual([]);
    expect(await filesFromDrag({})).toEqual([]);
    expect(await filesFromDrag(drag([null]))).toEqual([]);
  });

  it("stops at the limit rather than uploading a whole drive", async () => {
    const many = Array.from({ length: DROP_LIMIT + 50 }, (_, i) => fileEntry(`f${i}.png`));
    expect(await filesFromDrag(drag([dirEntry("lots", many)]))).toHaveLength(DROP_LIMIT);
  });
});
