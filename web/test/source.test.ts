// Phase 9a: the script language, lint mapping, shot spans, the conflict flow of
// an editor window, promote, and the mock's source routes.
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { StringStream } from "@codemirror/language";
import { Text } from "@codemirror/state";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { ApiError, type Api } from "../src/api";
import { minimalChange } from "../src/components/CodeEditor";
import { PromoteView, UnifiedDiff } from "../src/components/Promote";
import { codeSpans, confirmPromote, initialChecks, scopeLabel, splitDiffNotice, tickedIds } from "../src/lib/promote";
import { classifyLine, scriptParser, type ScriptToken } from "../src/lib/scriptLang";
import {
  checkCounts, diffStats, isOwnMessage, listedMessages, localShotSpans, parseUnifiedDiff, shotAtLine, spansFor, toDiagnostics, unifiedDiff,
} from "../src/lib/source";
import { SourceSession } from "../src/lib/sourceSession";
import { createMockApi } from "../src/mock/mockApi";
import { checkScript, setShotLine } from "../src/mock/mockSource";
import type { PromotePlan, SourceCheck, SourceDoc } from "../src/types";

// ---------------------------------------------------------------------------
// the script language
// ---------------------------------------------------------------------------

/** [text, token] runs of a line, whitespace-only plain runs dropped. */
function runs(line: string): [string, ScriptToken | null][] {
  const out: [string, ScriptToken | null][] = [];
  let at = 0;
  for (const s of classifyLine(line)) {
    const text = line.slice(at, at + s.len);
    at += s.len;
    if (s.tok || text.trim()) out.push([text, s.tok]);
  }
  expect(at).toBe(line.length); // the runs cover the line exactly
  return out;
}

/** Tokens as CodeMirror's StreamLanguage would get them. */
function stream(line: string): [string, string | null][] {
  const st = scriptParser.startState!(2);
  const s = new StringStream(line, 2, 2);
  const out: [string, string | null][] = [];
  while (!s.eol()) {
    s.start = s.pos;
    const tok = scriptParser.token(s, st);
    expect(s.pos).toBeGreaterThan(s.start); // always advances
    out.push([s.current(), tok]);
  }
  return out;
}

describe("the script StreamLanguage", () => {
  it("reads the episode, sequence and shot headers", () => {
    expect(runs("= ep05  Dean and the Healing Spring")).toEqual([
      ["=", "mark"], ["ep05", "episodeId"], ["Dean and the Healing Spring", "episodeTitle"],
    ]);
    expect(runs("# sq01  workshop_wide")).toEqual([["#", "mark"], ["sq01", "sequenceId"], ["workshop_wide", "location"]]);
    expect(runs("## sh010")).toEqual([["##", "mark"], ["sh010", "shotId"]]);
    // `###` is not a header (the parser reads it as action)
    expect(runs("### not a header")).toEqual([["### not a header", null]]);
  });

  it("reads fields by key: names, numbers, words and prose", () => {
    expect(runs("who: dean, bolt")).toEqual([["who", "key"], [": ", "punct"], ["dean", "names"], [",", "punct"], ["bolt", "names"]]);
    expect(runs("audio: 3.10-7.40")).toEqual([["audio", "key"], [": ", "punct"], ["3.10", "number"], ["-", "value"], ["7.40", "number"]]);
    expect(runs("dur: auto")).toEqual([["dur", "key"], [": ", "punct"], ["auto", "value"]]);
    expect(runs("camera: pushes in at slow speed")).toEqual([["camera", "key"], [": ", "punct"], ["pushes in at slow speed", "prose"]]);
    expect(runs("target: wan22_i2v")).toEqual([["target", "key"], [": ", "punct"], ["wan", "value"], ["22", "number"], ["_i", "value"], ["2", "number"], ["v", "value"]]);
  });

  it("marks a lower-case `word:` that isn't a field (the parser takes it as action)", () => {
    expect(runs("sise: wide")[0]).toEqual(["sise", "unknownKey"]);
  });

  it("reads dialogue: the speaker, the parenthetical with its voice markers, the line", () => {
    expect(runs("NARRATOR (V.O., into phone): Nobody ever goes left.")).toEqual([
      ["NARRATOR", "speaker"], ["(", "paren"], ["V.O.", "voice"], [",", "paren"], [" ", "paren"], ["into phone", "paren"], [")", "paren"],
      [": ", "punct"], ["Nobody ever goes left.", "dialogue"],
    ]);
    expect(runs("BO: I know.")).toEqual([["BO", "speaker"], [": ", "punct"], ["I know.", "dialogue"]]);
    expect(runs("ADA (warmly): You know that one whistles.")[2]).toEqual(["warmly", "paren"]);
    expect(runs("BO (O.S.): Left! Left!")[2]).toEqual(["O.S.", "voice"]);
    expect(runs("MR SMITH: Hello.")[0]).toEqual(["MR SMITH", "speaker"]);
  });

  it("takes comments (indented too) and action prose as the parser does", () => {
    expect(runs("// a comment")).toEqual([["// a comment", "comment"]]);
    expect(runs("   // indented")).toEqual([["   // indented", "comment"]]);
    expect(runs("Dean digs through a heap of scrap gears.")).toEqual([["Dean digs through a heap of scrap gears.", null]]);
    // indented dialogue isn't dialogue (the parser doesn't strip the left)
    expect(runs("  BO: hi")).toEqual([["  BO: hi", null]]);
  });

  it("feeds CodeMirror a token per run", () => {
    expect(stream("## sh010")).toEqual([["##", "mark"], [" ", null], ["sh010", "shotId"]]);
    expect(stream("who: dean")).toEqual([["who", "key"], [": ", "punct"], ["dean", "names"]]);
  });
});

// ---------------------------------------------------------------------------
// spans and lint
// ---------------------------------------------------------------------------

const SCRIPT = `= ks01  Kitchen Sink

# sq01  kitchen
Scene-setting prose.

## sh010
who: ada
size: ws
Ada slams the door.

// a comment between shots

## sh020
who: bo
BO: I know.


# sq02  yard

## sh030
who: ada
Ada waves.
`;

describe("shot spans", () => {
  it("run from the header to the shot's last line, like the parser's", () => {
    expect(localShotSpans(SCRIPT)).toEqual([
      { id: "sh010", line: 6, end_line: 9 },
      { id: "sh020", line: 13, end_line: 15 },
      { id: "sh030", line: 20, end_line: 22 },
    ]);
  });
  it("find the shot at a line (none between shots)", () => {
    const sp = localShotSpans(SCRIPT);
    expect(shotAtLine(sp, 8)).toBe("sh010");
    expect(shotAtLine(sp, 11)).toBeNull();
    expect(shotAtLine(sp, 13)).toBe("sh020");
  });
  it("trust a check only for the text it checked", () => {
    const result: SourceCheck = { ok: true, errors: [], warnings: [], shots: [{ id: "x", line: 1, end_line: 2 }] };
    expect(spansFor(SCRIPT, { text: SCRIPT, result })).toBe(result.shots);
    expect(spansFor(SCRIPT, { text: "older", result })[0].id).toBe("sh010");
    expect(spansFor(SCRIPT, { text: SCRIPT, result: { ...result, shots: [] } })[0].id).toBe("sh010");
  });
});

describe("check messages as diagnostics", () => {
  const doc = Text.of(SCRIPT.split("\n"));
  const check: SourceCheck = {
    ok: false,
    errors: [
      { file: "script", line: 7, message: "'ada' is not a subject" },
      { file: "script", line: 8, col: 7, message: "size 'ws' …" },
      { file: "series", line: 3, col: 5, message: "Expecting ',' delimiter" },
      { file: "script", line: 999, message: "past the end" },
    ],
    warnings: [{ file: "ep05.md", line: 14, message: "dialogue may not fit" }],
    shots: [],
  };
  it("map lines (and cols) to ranges, this file's only", () => {
    const d = toDiagnostics(doc, check, "script", "ep05.md");
    expect(d).toHaveLength(4);
    const l7 = doc.line(7);
    expect(d[0]).toEqual({ from: l7.from, to: l7.to, severity: "error", message: "'ada' is not a subject" });
    // col 7 of "size: ws" is the value: underline that word
    const l8 = doc.line(8);
    expect(doc.sliceString(d[1].from, d[1].to)).toBe("ws");
    expect(d[1].from).toBe(l8.from + 6);
    // a line past the end lands on the last line
    expect(d[2].from).toBe(doc.line(doc.lines).from);
    expect(d[3]).toMatchObject({ severity: "warning", message: "dialogue may not fit" });
  });
  it("list the other file's messages separately", () => {
    expect(listedMessages(check, "script", "ep05.md")).toEqual([{ file: "series", line: 3, col: 5, message: "Expecting ',' delimiter", severity: "error", own: false }]);
    expect(toDiagnostics(doc, check, "series", "series.json")).toHaveLength(1);
  });
  it("list a message with no line (as built: a series config message naming no key)", () => {
    const c: SourceCheck = { ok: false, errors: [{ file: "series", line: null, message: "series.json has no `subjects` block" }], warnings: [], shots: [] };
    const sdoc = Text.of(["{", "}"]);
    expect(toDiagnostics(sdoc, c, "series", "series.json")).toEqual([]);
    expect(listedMessages(c, "series", "series.json")).toEqual([{ ...c.errors[0], severity: "error", own: true }]);
    expect(checkCounts(c)).toBe("1 error");
  });
  it("match a message's file by kind, path or extension", () => {
    expect(isOwnMessage({ file: "" }, "series")).toBe(true);
    expect(isOwnMessage({ file: "../series.json" }, "series", "../series.json")).toBe(true);
    expect(isOwnMessage({ file: "C:\\Shows\\ep05\\ep05.md" }, "script", "ep05.md")).toBe(true);
    expect(isOwnMessage({ file: "ep05.md" }, "series", "series.json")).toBe(false);
  });
  it("count them for the window header", () => {
    expect(checkCounts(check)).toBe("4 errors · 1 warning");
    expect(checkCounts({ ok: true, errors: [], warnings: [], shots: [] })).toBe("");
  });
});

describe("minimalChange (a reload that keeps the cursor)", () => {
  it("touches only the middle that differs", () => {
    expect(minimalChange("abc\nsize: wide\nxyz", "abc\nsize: close\nxyz")).toEqual({ from: 10, to: 13, insert: "clos" });
    expect(minimalChange("same", "same")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// the editor window's session: live check, save, outside edits, 409
// ---------------------------------------------------------------------------

/** A fake server holding one file. */
function fakeServer(text: string) {
  const disk = { text, hash: "h1", n: 1 };
  const calls: string[] = [];
  const doc = (): SourceDoc => ({ file: "script", path: "ep05.md", text: disk.text, hash: disk.hash, mtime: disk.n, shots: [] });
  const api = {
    source: async () => (calls.push("source"), doc()),
    sourceHash: async () => (calls.push("hash"), { file: "script", hash: disk.hash, mtime: disk.n }),
    checkSource: async (_ep: string, _f: string, t: string) => {
      calls.push("check");
      return { ok: !t.includes("BAD"), errors: t.includes("BAD") ? [{ file: "script", line: 1, message: "bad" }] : [], warnings: [], shots: [] };
    },
    putSource: async (req: { text: string; base_hash: string }) => {
      calls.push(`put:${req.base_hash}`);
      if (req.base_hash !== disk.hash) throw new ApiError("changed on disk", 409, "/h3pipe/source", { error: "changed on disk", hash: disk.hash, text: disk.text });
      disk.text = req.text;
      disk.hash = `h${++disk.n}`;
      return { hash: disk.hash, check: { ok: true, errors: [], warnings: [], shots: [] }, build: { ok: true, passes: {} } };
    },
  } as unknown as Api;
  const outside = (t: string) => {
    disk.text = t;
    disk.hash = `h${++disk.n}`;
  };
  return { api, disk, calls, outside };
}

function session(text = "one\n") {
  const srv = fakeServer(text);
  const replaced: [string, string][] = [];
  const saved = vi.fn();
  const s = new SourceSession(srv.api, "C:/ep05", "script", { replace: (t, why) => replaced.push([t, why]), saved });
  return { s, srv, replaced, saved };
}

describe("SourceSession", () => {
  it("loads, marks dirty on edit, and checks ~400 ms after the last keystroke", async () => {
    vi.useFakeTimers();
    try {
      const { s, srv, replaced } = session();
      await s.load();
      expect(replaced).toEqual([["one\n", "load"]]);
      await vi.runAllTimersAsync();
      srv.calls.length = 0;
      s.edit("one\nBAD");
      s.edit("one\nBAD2");
      expect(s.get().dirty).toBe(true);
      await vi.advanceTimersByTimeAsync(399);
      expect(srv.calls).toEqual([]);
      await vi.advanceTimersByTimeAsync(1);
      expect(srv.calls).toEqual(["check"]);
      expect(s.get().check?.text).toBe("one\nBAD2");
      expect(s.get().check?.result.errors).toHaveLength(1);
      s.edit("one\n"); // back to the disk's text
      expect(s.get().dirty).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });

  it("saves with base_hash and takes the new hash", async () => {
    const { s, srv, saved } = session();
    await s.load();
    s.edit("two\n");
    const r = await s.save();
    expect(r?.hash).toBe("h2");
    expect(srv.calls).toContain("put:h1");
    expect(s.get()).toMatchObject({ baseHash: "h2", dirty: false, conflict: null, saved: { built: true } });
    expect(saved).toHaveBeenCalledOnce();
    expect(await s.poll()).toBe("same");
  });

  it("reloads an outside edit silently when the buffer is clean", async () => {
    const { s, srv, replaced } = session();
    await s.load();
    srv.outside("edited elsewhere\n");
    expect(await s.poll()).toBe("reloaded");
    expect(replaced.at(-1)).toEqual(["edited elsewhere\n", "outside"]);
    expect(s.get()).toMatchObject({ text: "edited elsewhere\n", baseHash: "h2", dirty: false });
  });

  it("offers Reload / Keep mine when the buffer is dirty; Keep mine saves over it", async () => {
    const { s, srv, replaced } = session();
    await s.load();
    s.edit("mine\n");
    srv.outside("theirs\n");
    expect(await s.poll()).toBe("conflict");
    expect(s.get().conflict).toEqual({ hash: "h2", text: "theirs\n", reason: "disk" });
    expect(await s.poll()).toBe("same"); // no second banner for the same change
    await s.keepMine();
    expect(s.get()).toMatchObject({ conflict: null, baseHash: "h2", dirty: true, text: "mine\n" });
    await s.save();
    expect(srv.disk.text).toBe("mine\n");
    expect(replaced.map((r) => r[1])).toEqual(["load"]); // the buffer was never replaced
  });

  it("Reload drops the buffer for the disk's text", async () => {
    const { s, srv, replaced } = session();
    await s.load();
    s.edit("mine\n");
    srv.outside("theirs\n");
    await s.poll();
    s.reload();
    expect(replaced.at(-1)).toEqual(["theirs\n", "reload"]);
    expect(s.get()).toMatchObject({ conflict: null, dirty: false, baseHash: "h2", text: "theirs\n" });
  });

  it("a 409 on save is a conflict; Keep mine resends with the new hash", async () => {
    const { s, srv } = session();
    await s.load();
    s.edit("mine\n");
    srv.outside("theirs\n"); // not polled yet
    expect(await s.save()).toBeNull();
    expect(s.get().conflict).toEqual({ hash: "h2", text: "theirs\n", reason: "save" });
    await s.keepMine();
    expect(srv.calls.filter((c) => c.startsWith("put:"))).toEqual(["put:h1", "put:h2"]);
    expect(srv.disk.text).toBe("mine\n");
    expect(s.get()).toMatchObject({ conflict: null, dirty: false, baseHash: "h3" });
  });
});

// ---------------------------------------------------------------------------
// promote
// ---------------------------------------------------------------------------

const PLAN: PromotePlan = {
  items: [
    { id: "shot:sh040:target", scope: "shot", shot: "sh040", field: "target", value: "ltx2", dest: "script", line: 42, summary: "sh040: render on ltx2" },
    { id: "episode:target", scope: "episode", field: "target", value: "wan22_i2v", dest: "series", summary: "The episode renders on wan22_i2v" },
  ],
  left: [{ scope: "shot", shot: "sh040", field: "seed", reason: "a seed is kept by picking the take" }],
  diffs: {
    script: "--- a/ep05.md\n+++ b/ep05.md\n@@ -41,2 +41,3 @@\n size: medium\n+target: ltx2\n dur: 3.04\n",
    series: "--- a/series.json\n+++ b/series.json\n@@ -3,3 +3,3 @@\n     \"title\": \"x\",\n-    \"fps\": 24\n+    \"fps\": 24,\n+    \"target\": \"wan22_i2v\"\n",
  },
  hashes: { script: "s1", series: "c1" },
};

describe("unified diffs", () => {
  it("parse into numbered +/- lines", () => {
    const l = parseUnifiedDiff(PLAN.diffs.script);
    expect(l.map((x) => x.kind)).toEqual(["file", "file", "hunk", "ctx", "add", "ctx"]);
    expect(l[4]).toEqual({ kind: "add", text: "target: ltx2", new: 42 });
    expect(l[5]).toMatchObject({ old: 42, new: 43 });
    expect(diffStats(PLAN.diffs.series)).toEqual({ add: 2, del: 1 });
  });
  it("a removed '-- x' line inside a hunk isn't a file header", () => {
    expect(parseUnifiedDiff("--- a\n+++ b\n@@ -1,1 +1,1 @@\n--- x\n++++ y\n").map((x) => x.kind)).toEqual(["file", "file", "hunk", "del", "add"]);
  });
  it("the mock's differ writes what the parser reads", () => {
    const d = unifiedDiff("a\nb\nc\nd\n", "a\nB\nc\nd\ne\n", "a/f", "b/f");
    expect(d).toContain("-b\n+B\n");
    expect(diffStats(d)).toEqual({ add: 2, del: 1 });
  });
  it("show a leading `# …` line (as built: the series config wasn't formatted) as a notice", () => {
    const d = "# series.json wasn't formatted with a 2-space indent: promoting rewrites the whole file that way\n" + PLAN.diffs.series;
    expect(splitDiffNotice(d).notice).toEqual(["series.json wasn't formatted with a 2-space indent: promoting rewrites the whole file that way"]);
    const html = renderToStaticMarkup(createElement(UnifiedDiff, { diff: d }));
    expect(html).toContain('<div class="h3-note h3-small">series.json wasn');
    expect(html).not.toContain("h3-dl-note");
  });
  it("summaries' backticks are code; a left entry names its pass", () => {
    expect(codeSpans("sh030: `lora: x:0.5` (both passes)")).toEqual([
      { text: "sh030: ", code: false }, { text: "lora: x:0.5", code: true }, { text: " (both passes)", code: false },
    ]);
    expect(scopeLabel({ scope: "shot", shot: "sh050", pass: "final" })).toBe("sh050 · final");
    expect(scopeLabel({ scope: "ref", ref: "subject:dean" })).toBe("dean");
  });
  it("render coloured rows", () => {
    const html = renderToStaticMarkup(createElement(UnifiedDiff, { diff: PLAN.diffs.script }));
    expect(html).toContain("h3-dl-add");
    expect(html).toContain("target: ltx2");
    expect(html.match(/h3-dl h3-dl-ctx/g)).toHaveLength(2);
  });
});

describe("the promote dialog", () => {
  it("ticks every item, keeps earlier choices on a re-plan", () => {
    const c = initialChecks(PLAN);
    expect(tickedIds(PLAN, c)).toEqual(["shot:sh040:target", "episode:target"]);
    const again = initialChecks(PLAN, { "episode:target": false });
    expect(tickedIds(PLAN, again)).toEqual(["shot:sh040:target"]);
  });

  it("shows the items, what stays and why, and both diffs", () => {
    const html = renderToStaticMarkup(createElement(PromoteView, {
      plan: PLAN, checked: { "episode:target": false }, onToggle: () => {}, onAll: () => {},
      names: { script: "ep05.md", series: "series.json" }, dirty: { script: true, series: false },
    }));
    expect(html).toContain("Promote (1 of 2)");
    expect(html).toContain("sh040: render on ltx2");
    expect(html).toContain("ep05.md:42");
    expect(html).toContain("Stays in overrides.json (1)");
    expect(html).toContain("a seed is kept by picking the take");
    expect(html).toContain("ep05.md has unsaved edits");
    expect(html).toContain("the unticked ones are left out");
    expect(html).toContain("+2");
  });

  it("confirms with the plan's hashes; a 409 fetches a fresh plan", async () => {
    const fresh = { ...PLAN, hashes: { script: "s2", series: "c1" } };
    const promote = vi.fn()
      .mockRejectedValueOnce(new ApiError("changed on disk", 409, "/h3pipe/promote", { error: "changed on disk", hash: "s2" }))
      .mockResolvedValueOnce({ promoted: ["episode:target"], left: [], hashes: fresh.hashes, build: null });
    const promotePlan = vi.fn().mockResolvedValue(fresh);
    const api = { promote, promotePlan } as unknown as Api;
    const a = await confirmPromote(api, "ep", null, PLAN, ["episode:target"]);
    expect(promote).toHaveBeenLastCalledWith("ep", ["episode:target"], { script: "s1", series: "c1" }, null);
    expect(a).toEqual({ kind: "replanned", plan: fresh, why: "changed on disk" });
    const b = await confirmPromote(api, "ep", null, fresh, ["episode:target"]);
    expect(promote).toHaveBeenLastCalledWith("ep", ["episode:target"], { script: "s2", series: "c1" }, null);
    expect(b.kind).toBe("done");
    promote.mockRejectedValueOnce(new ApiError("boom", 500, "/h3pipe/promote"));
    expect(await confirmPromote(api, "ep", null, fresh, [])).toEqual({ kind: "error", message: "boom" });
  });
});

// ---------------------------------------------------------------------------
// the mock's source routes
// ---------------------------------------------------------------------------

describe("mock source routes", () => {
  it("serve a script built from the fixture episode that checks clean against its series config", async () => {
    const api = createMockApi(() => {}, { latency: 0 });
    const ep = (await api.episodes())[0].ep;
    const doc = await api.source(ep, "script");
    expect(doc.path).toBe("ep05.md");
    expect(doc.text.startsWith("= ep05  ")).toBe(true);
    const c = await api.checkSource(ep, "script", doc.text);
    expect(c.errors).toEqual([]);
    const st = await api.episode(ep, "proxy");
    expect(doc.shots.map((s) => s.id)).toEqual(st.shots.filter((s) => !s.orphan).map((s) => s.shot));
    // real-looking lines: dialogue, fields, the script's targets
    expect(doc.text).toMatch(/^NARRATOR \(V\.O\.\): /m);
    expect(doc.text).toMatch(/^who: dean/m);
    expect(doc.text).toMatch(/^target: wan22_i2v$/m);
    const series = await api.source(ep, "series");
    expect(JSON.parse(series.text).subjects.dean.kind).toBe("character");
    expect((await api.checkSource(ep, "series", series.text)).ok).toBe(true);
  });

  it("report errors at lines (and a JSON error's col)", async () => {
    const api = createMockApi(() => {}, { latency: 0 });
    const ep = (await api.episodes())[0].ep;
    const doc = await api.source(ep, "script");
    const bad = doc.text.replace("who: dean", "who: deen");
    const c = await api.checkSource(ep, "script", bad);
    const line = bad.split("\n").findIndex((l) => l.startsWith("who: deen")) + 1;
    expect(c.errors[0]).toMatchObject({ file: "script", line, col: 6 });
    expect(c.shots).toEqual([]);
    const series = await api.source(ep, "series");
    const broken = series.text.replace('"fps": 24,', '"fps": 24');
    const j = await api.checkSource(ep, "series", broken);
    expect(j.ok).toBe(false);
    expect(j.errors[0].file).toBe("series");
    expect(j.errors[0].line).toBeGreaterThan(1);
    await expect(api.putSource({ ep, file: "series", text: broken, base_hash: series.hash, rebuild: true })).rejects.toMatchObject({ status: 400 });
  });

  it("save: 409 with the disk's hash and text after an outside edit", async () => {
    const api = createMockApi(() => {}, { latency: 0 });
    const ep = (await api.episodes())[0].ep;
    const doc = await api.source(ep, "script");
    api.outsideEdit("script", (t) => t.replace("size: wide", "size: close"));
    const h = await api.sourceHash(ep, "script");
    expect(h.hash).not.toBe(doc.hash);
    const err = await api.putSource({ ep, file: "script", text: doc.text + "\n", base_hash: doc.hash, rebuild: true }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(409);
    expect(err.data).toMatchObject({ hash: h.hash });
    expect(err.data.text).toContain("size: close");
    const ok = await api.putSource({ ep, file: "script", text: doc.text, base_hash: h.hash, rebuild: true });
    expect(ok.build?.ok).toBe(true);
    expect((await api.sourceHash(ep, "script")).hash).toBe(ok.hash);
  });

  it("promote moves a shot's target into its block and drops the override", async () => {
    const api = createMockApi(() => {}, { latency: 0 });
    const ep = (await api.episodes())[0].ep;
    // sh030 is retargeted to LTX-2 in the mock (an override); it also has a prompt override
    const plan = await api.promotePlan(ep, "sh030");
    const it0 = plan.items.find((i) => i.id === "shot:sh030:target")!;
    expect(it0).toMatchObject({ dest: "script", field: "target" });
    expect(plan.left.map((l) => l.field)).toContain("prompt");
    expect(plan.diffs.script).toContain("+target: ltx2");
    const r = await api.promote(ep, [it0.id], plan.hashes);
    expect(r.promoted).toEqual([it0.id]);
    const d = await api.shot(ep, "proxy", "sh030");
    expect(d.override.target).toBeUndefined();
    expect(d.target_source).toBe("script");
    const doc = await api.source(ep, "script");
    const sp = doc.shots.find((s) => s.id === "sh030")!;
    expect(doc.text.split("\n").slice(sp.line - 1, sp.end_line)).toContain(`target: ${d.target}`);
    // the old plan's hashes are stale now
    await expect(api.promote(ep, "all", plan.hashes)).rejects.toMatchObject({ status: 409 });
  });

  it("setShotLine replaces the shot's own line, else inserts after its last field", () => {
    const a = setShotLine(SCRIPT, "sh010", "size", "close")!;
    expect(a.line).toBe(8);
    expect(a.text.split("\n")[7]).toBe("size: close");
    const b = setShotLine(SCRIPT, "sh020", "target", "ltx2")!;
    expect(b.line).toBe(15);
    expect(b.text.split("\n").slice(12, 16)).toEqual(["## sh020", "who: bo", "target: ltx2", "BO: I know."]);
  });

  it("the mock check flags unknown speakers and duplicate shot ids", () => {
    const cfg = { subjects: { ada: { kind: "character" }, bo: { kind: "character" } }, locations: { kitchen: {}, yard: {} } };
    expect(checkScript(SCRIPT, cfg).errors).toEqual([]);
    const bad = SCRIPT.replace("BO: I know.", "CY: I know.").replace("## sh030", "## sh010");
    const msgs = checkScript(bad, cfg).errors.map((e) => `${e.line}: ${e.message}`);
    expect(msgs[0]).toMatch(/^15: 'CY' is not a character/);
    expect(msgs[1]).toMatch(/^20: shot id 'sh010' is already used on line 6/);
  });
});

// ---------------------------------------------------------------------------
// real files: test/local/<name>/ (gitignored) holds a scratch copy of a real
// episode's script + series.json, and spans.json from h3core.story._parse
// ---------------------------------------------------------------------------

const LOCAL = join(dirname(fileURLToPath(import.meta.url)), "local");
const realDirs = existsSync(LOCAL)
  ? readdirSync(LOCAL).map((d) => join(LOCAL, d)).filter((d) => existsSync(join(d, "series.json")) && readdirSync(d).some((f) => /^ep\d+.*\.md$/.test(f)))
  : [];

describe.skipIf(!realDirs.length)("a real script and series config", () => {
  for (const dir of realDirs) {
    const md = readdirSync(dir).find((f) => /^ep\d+.*\.md$/.test(f))!;
    const script = readFileSync(join(dir, md), "utf-8");
    const seriesText = readFileSync(join(dir, "series.json"), "utf-8");
    it(`${md}: every line tokenizes, and the classes match the parser's reading`, () => {
      const counts: Record<string, number> = {};
      for (const line of script.split(/\r?\n/)) {
        const r = runs(line);
        const first = r[0]?.[1] ?? "blank";
        counts[first] = (counts[first] ?? 0) + 1;
        if (/^## /.test(line)) expect(r[1][1]).toBe("shotId");
        if (/^[A-Z][A-Z0-9_ '-]*?\s*(\([^)]*\))?\s*:\s*\S/.test(line)) expect(first).toBe("speaker");
      }
      expect(counts.shotId ?? 0).toBe(0); // (the id is the second run)
      expect(counts.mark).toBeGreaterThan(10);
      expect(counts.key).toBeGreaterThan(50);
      expect(counts.speaker).toBeGreaterThan(5);
      expect(counts.comment).toBeGreaterThan(0);
    });
    it(`${md}: local shot spans are the parser's`, () => {
      const spansFile = join(dir, "spans.json");
      if (!existsSync(spansFile)) return;
      expect(localShotSpans(script)).toEqual(JSON.parse(readFileSync(spansFile, "utf-8")));
    });
    // answers of the real routes' functions (h3source / h3promote) on a scratch copy,
    // dumped by the session's scratch script into the same folder
    const answer = <T,>(name: string): T | null => (existsSync(join(dir, name)) ? JSON.parse(readFileSync(join(dir, name), "utf-8")) : null);
    it(`${md}: real source answers: shots are the local scan's, messages land on their lines`, () => {
      const doc = answer<SourceDoc>("source_script.json");
      if (!doc) return;
      expect(doc.text.includes("\r")).toBe(false); // as built: LF, no BOM
      expect(localShotSpans(doc.text)).toEqual(doc.shots);
      const bad = answer<SourceCheck>("check_script_bad.json")!;
      const badText = doc.text.replace("who: dean", "who: deen");
      const d = toDiagnostics(Text.of(badText.split("\n")), bad, "script", doc.path);
      expect(d).toHaveLength(1);
      expect(badText.slice(d[0].from, d[0].to)).toMatch(/^who: deen/);
      const sdoc = answer<SourceDoc>("source_series.json")!;
      const js = answer<SourceCheck>("check_series_json.json")!;
      const broken = sdoc.text.replace('"fps": 24,', '"fps": 24');
      const jd = toDiagnostics(Text.of(broken.split("\n")), js, "series", sdoc.path);
      expect(jd).toHaveLength(1);
      expect(jd[0].to).toBeGreaterThan(jd[0].from);
      const none = answer<SourceCheck>("check_series_nosubjects.json")!;
      expect(toDiagnostics(Text.of(["{}"]), none, "series", sdoc.path)).toEqual([]);
      expect(listedMessages(none, "series", sdoc.path)).toHaveLength(1);
    });
    it(`${md}: a real promote plan renders (items, what stays with its pass, the diff)`, () => {
      const plan = answer<PromotePlan>("promote_plan.json");
      if (!plan) return;
      const html = renderToStaticMarkup(createElement(PromoteView, {
        plan, checked: initialChecks(plan), onToggle: () => {}, onAll: () => {}, names: { script: md, series: "series.json" },
      }));
      expect(html).toContain(`Promote (${plan.items.length} of ${plan.items.length})`);
      expect(html).toContain(`Stays in overrides.json (${plan.left.length})`);
      expect(html).toContain('<code class="h3-mono h3-code-span">');
      if (plan.left.some((l) => l.pass)) expect(html).toMatch(/sh\d+ · (final|proxy)/);
      const diff = renderToStaticMarkup(createElement(UnifiedDiff, { diff: plan.diffs.script }));
      expect(diff.match(/h3-dl h3-dl-add/g)?.length).toBe(diffStats(plan.diffs.script).add);
    });
    it(`${md}: the mock's check agrees the real pair is clean, and a typo lands on its line`, () => {
      const cfg = JSON.parse(seriesText);
      expect(checkScript(script, cfg).errors).toEqual([]);
      const lines = script.split("\n");
      const i = lines.findIndex((l) => l.startsWith("who: "));
      lines[i] = lines[i].replace("who: ", "who: nobody_here, ");
      const c = checkScript(lines.join("\n"), cfg);
      expect(c.errors[0].line).toBe(i + 1);
      const d = toDiagnostics(Text.of(lines), c, "script", md);
      expect(lines.join("\n").slice(d[0].from, d[0].to)).toBe("nobody_here,");
    });
  }
});
