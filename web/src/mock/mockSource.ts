// Phase 9a for the dev page: the mock episode's two authored files and what the
// server does with them (check, save, promote).
//
// - The script is rebuilt from the fixtures (the real ep05 shotlist): sequence
//   and shot ids, who:, size:, dur:, plate:, the action, camera, dialogue,
//   sound and music read back out of each shot's compiled prompt, plus the
//   mock's `target:` / `first:` / `last:` / `dur: model` lines.
// - The series config is the kitchen_sink fixture (tests/fixtures), which the
//   mock refs are built from, with the episode's own subjects and locations
//   (read out of the same prompts) added, so the script checks clean.
// - The check is a TypeScript subset of h3core/story.py (the same line rules and
//   messages), not the real `h3.py check`.

import seriesRaw from "../../../tests/fixtures/kitchen_sink/series.json?raw";
import { localShotSpans, unifiedDiff } from "../lib/source";
import type { Lora, Override, PromoteItem, PromoteLeft, SourceCheck, SourceMessage } from "../types";

// ---------------------------------------------------------------------------
// hashing (opaque to the UI; sha1-sized hex)
// ---------------------------------------------------------------------------

export function mockHash(text: string): string {
  let out = "";
  for (let k = 0; k < 5; k++) {
    let h = 2166136261 ^ (k * 0x9e3779b9);
    for (let i = 0; i < text.length; i++) h = Math.imul(h ^ text.charCodeAt(i), 16777619);
    out += (h >>> 0).toString(16).padStart(8, "0");
  }
  return out;
}

// ---------------------------------------------------------------------------
// building the files from the fixtures
// ---------------------------------------------------------------------------

export interface MockShotSource {
  shot: string;
  sequence: string;
  subjects: string[];
  size: string | null;
  seconds: number;
  /** refs/_bg/<plate>.png */
  background: string | null;
  prompt: string;
  voices: string[];
}

export interface ScriptExtras {
  targets: Record<string, string>;
  keyframes: Record<string, Partial<Record<"first" | "last", string>>>;
  durModel: Set<string>;
}

const plateOf = (bg: string | null) => (bg ? bg.split("/").pop()!.replace(/\.\w+$/, "") : null);

function section(prompt: string, name: string): string {
  const m = new RegExp(`(?:^|\\n)${name}:\\s*([\\s\\S]*?)(?=\\n\\n[a-z_]+:|\\n[a-z_]+:\\s|$)`).exec(prompt);
  return m ? m[1].trim() : "";
}

const sentence = (s: string) => s.replace(/\s+/g, " ").replace(/\.$/, "").trim();
const lowerFirst = (s: string) => (s ? s[0].toLowerCase() + s.slice(1) : s);

/** The shot's lines, read back out of its compiled H3 prompt. */
export function shotLines(s: MockShotSource, extras: ScriptExtras): string[] {
  const p = s.prompt;
  const out = [`## ${s.shot}`];
  const cast = s.subjects.filter((x) => x !== "narrator");
  if (cast.length) out.push(`who: ${cast.join(", ")}`);
  if (s.size) out.push(`size: ${s.size}`);
  const summary = section(p, "summary").replace(/^\[[^\]]*\]\s*/, "");
  const said = [...p.matchAll(/\(S(\d+)\) says([^:<]*):\s*<d>(?:\[[^\]]*\]\s*)?([\s\S]*?)<\/d>/g)];
  if (extras.durModel.has(s.shot)) out.push("dur: model");
  else out.push(`dur: ${said.length ? "auto" : (Math.round(s.seconds * 100) / 100).toFixed(2)}`);
  const plate = plateOf(s.background);
  if (plate) out.push(`plate: ${plate}`);
  if (extras.targets[s.shot]) out.push(`target: ${extras.targets[s.shot]}`);
  for (const w of ["first", "last"] as const) if (extras.keyframes[s.shot]?.[w]) out.push(`${w}: ${extras.keyframes[s.shot]![w]}`);
  if (summary) out.push(sentence(summary) + ".");
  const cam = /The camera ([^.]+)\./.exec(p);
  if (cam) out.push(`camera: ${cam[1].trim()}`);
  for (const m of said) {
    const who = (s.voices[Number(m[1]) - 1] ?? s.voices[0] ?? "narrator").toUpperCase();
    const how = m[2];
    const tag = /voiceover/.test(how) ? " (V.O.)" : /off-screen/.test(how) ? " (O.S.)" : "";
    out.push(`${who}${tag}: ${m[3].trim()}`);
  }
  const sound = section(p, "overall_soundscape");
  if (sound) out.push(`sound: ${lowerFirst(sentence(sound))}`);
  const music = section(p, "non_diegetic_music");
  if (music && !/^n\/?a\.?$/i.test(music)) out.push(`music: ${sentence(music)}`);
  return out;
}

export function buildScript(ep: { id: string; title: string }, shots: MockShotSource[], extras: ScriptExtras): string {
  const lines = [
    `= ${ep.id}  ${ep.title}`,
    "",
    "// The dev page's mock script: rebuilt from the fixture episode's shotlist.",
    "// Edit it here (or with h3mockEdit in the console, as if in another editor).",
  ];
  let seq: string | null = null;
  for (const s of shots) {
    if (s.sequence !== seq) {
      seq = s.sequence;
      lines.push("", "", `# ${seq}  ${plateOf(s.background) ?? "location"}`);
    }
    lines.push("", ...shotLines(s, extras));
  }
  return lines.join("\n") + "\n";
}

interface SeriesCfg {
  _README?: string;
  series: Record<string, unknown>;
  subjects: Record<string, Record<string, unknown> | string>;
  locations: Record<string, Record<string, unknown> | string>;
  [k: string]: unknown;
}

/** The kitchen_sink series config plus the fixture episode's subjects and locations. */
export function buildSeries(title: string, shots: MockShotSource[]): string {
  const cfg = JSON.parse(seriesRaw) as SeriesCfg;
  cfg._README = "The dev page's mock series config: the kitchen_sink fixture (its refs are the mock's) plus the fixture episode's subjects and locations.";
  cfg.series = { ...cfg.series, title };
  for (const s of shots) {
    const p = s.prompt;
    // "<Subject 1> is Dean, defined by <Picture 1> — … Preserve the exact reference styling: <design>."
    for (const m of p.matchAll(/<Subject (\d+)> is ([^\n]*?), defined by <Picture \d+> — ([^\n]*)/g)) {
      const i = Number(m[1]) - 1;
      const rest = m[3];
      if (m[2] === "the location") {
        const plate = plateOf(s.background);
        const d = /a background plate of (.*?)\. It establishes/.exec(rest);
        if (plate && !cfg.locations[plate]) cfg.locations[plate] = { description: d ? d[1] : plate.replace(/_/g, " "), plate: s.background! };
        continue;
      }
      const id = s.subjects.filter((x) => x !== "narrator")[i];
      if (!id || cfg.subjects[id]) continue;
      const design = /Preserve the exact reference styling: (.*?)\.?$/.exec(rest)?.[1] ?? "";
      cfg.subjects[id] = {
        kind: /ONE character/.test(rest) ? "character" : "prop", name: m[2], design, sheet: `refs/${id}/${id}_sheet_4panel.png`,
      };
    }
    for (const [n, v] of s.voices.entries()) {
      if (cfg.subjects[v]) continue;
      const m = new RegExp(`voice that is ([^\\n]*?) \\(S${n + 1}\\)`).exec(p);
      cfg.subjects[v] = { kind: "character", name: v === "narrator" ? "the narrator" : v, voice: m?.[1] ?? "", voice_sample: `audio/voices/${v}_sample.wav` };
    }
    const plate = plateOf(s.background);
    if (plate && !cfg.locations[plate]) cfg.locations[plate] = { description: plate.replace(/_/g, " "), plate: s.background! };
  }
  return JSON.stringify(cfg, null, 2) + "\n";
}

// ---------------------------------------------------------------------------
// checking (a subset of h3core/story.py)
// ---------------------------------------------------------------------------

const META_KEYS = new Set([
  "who", "cast", "with", "props", "size", "audio", "dur", "duration", "camera", "sound", "music", "policy", "continuous", "text",
  "pace", "plate", "retention", "model", "lora", "steps", "extras", "target", "profile", "first", "last",
]);
const SIZES = new Set(["close", "cu", "medium", "ms", "wide", "ws"]);
const PACES = new Set(["slow", "normal", "fast"]);
const KEYFRAMES = new Set(["continuity", "generate", "import", "none"]);
const WORDS_PER_SECOND = 2.6;

function subjectsOf(cfg: Partial<SeriesCfg>): { subjects: Set<string>; characters: Set<string>; locations: Set<string> } {
  const subjects = new Set<string>();
  const characters = new Set<string>();
  for (const [id, v] of Object.entries(cfg.subjects ?? {})) {
    if (id.startsWith("_")) continue;
    subjects.add(id);
    const kind = typeof v === "object" && v ? (v as { kind?: string }).kind : undefined;
    if (!kind || kind === "character") characters.add(id);
  }
  const locations = new Set(Object.keys(cfg.locations ?? {}).filter((k) => !k.startsWith("_")));
  return { subjects, characters, locations };
}

/** Check a script against a series config: every problem, not just the first. */
export function checkScript(text: string, cfg: Partial<SeriesCfg>, file = "script"): SourceCheck {
  const { subjects, characters, locations } = subjectsOf(cfg);
  const upper = new Map([...characters].map((c) => [c.toUpperCase(), c]));
  const errors: SourceMessage[] = [];
  const warnings: SourceMessage[] = [];
  const err = (line: number, message: string, col?: number) => errors.push({ file, line, message, ...(col ? { col } : {}) });
  const seen = new Map<string, number>();
  let header = false;
  let seq = false;
  let shot: { id: string; line: number; words: number; dur: number | null; body: boolean } | null = null;
  const closeShot = () => {
    if (!shot) return;
    if (!shot.body) err(shot.line, `shot '${shot.id}' has no action and no dialogue`);
    if (shot.dur != null && shot.words) {
      const need = shot.words / WORDS_PER_SECOND;
      if (need > shot.dur * 1.15) {
        warnings.push({ file, line: shot.line, message: `${shot.id}: its dialogue (${shot.words} words) needs about ${need.toFixed(1)}s at a normal pace; the shot is ${shot.dur}s (dur: auto fits it)` });
      }
    }
    shot = null;
  };
  text.split(/\r?\n/).forEach((raw, i) => {
    const n = i + 1;
    const line = raw.replace(/\s+$/, "");
    if (!line.trim() || line.trim().startsWith("//")) return;
    if (line.startsWith("= ")) {
      header = true;
      return;
    }
    if (line.startsWith("# ") && !line.startsWith("## ")) {
      closeShot();
      const parts = line.slice(2).split(/\s+/).filter(Boolean);
      if (parts.length < 2) err(n, "sequence needs an id and a location: `# sq01 street`");
      else if (!locations.has(parts[1])) err(n, `'${parts[1]}' is not in series.json's locations`, line.indexOf(parts[1], 2) + 1);
      seq = true;
      return;
    }
    if (line.startsWith("## ")) {
      if (!seq) err(n, "shot appears before any `# sequence`");
      closeShot();
      const id = line.slice(3).trim().split(/\s+/)[0] ?? "";
      if (seen.has(id)) err(n, `shot id '${id}' is already used on line ${seen.get(id)}; shot ids must be unique in an episode`, 4);
      else seen.set(id, n);
      shot = { id, line: n, words: 0, dur: null, body: false };
      return;
    }
    if (!seq) {
      err(n, "content before the first `# sequence`");
      return;
    }
    const d = /^([A-Z][A-Z0-9_ '\-]*?)\s*(?:\(([^)]*)\))?\s*:\s*(.+)$/.exec(line);
    if (d) {
      const name = d[1].trim();
      if (!upper.has(name.toUpperCase())) {
        const lower = name.toLowerCase();
        if (subjects.has(lower)) err(n, `'${lower}' is a prop in series.json, not a character — only characters can speak. Change its kind to 'character', or write this as action.`, 1);
        else err(n, `'${name}' is not a character in series.json (${[...characters].sort().join(", ")}). Typo, or write it as action.`, 1);
        if (shot) shot.body = true; // (the parser stops here: no "no dialogue" error on top)
        return;
      }
      if (!shot) err(n, "dialogue outside a `## shot`");
      else {
        shot.body = true;
        shot.words += d[3].trim().split(/\s+/).length;
      }
      return;
    }
    const m = /^([a-z_]+)\s*:\s*(.*)$/.exec(line);
    if (m && META_KEYS.has(m[1])) {
      const [, key, val] = m;
      const col = line.indexOf(":") + 2 + (val ? line.slice(line.indexOf(":") + 1).search(/\S/) : 0);
      if (key === "continuous" && shot) err(n, "`continuous:` belongs under `# sequence`, not a shot");
      else if (["size", "audio", "dur", "duration", "pace", "who", "cast", "with", "props"].includes(key) && !shot) err(n, `\`${key}:\` outside a \`## shot\``);
      else if (["who", "cast", "with", "props"].includes(key)) {
        for (const c of val.split(",").map((x) => x.trim()).filter(Boolean)) {
          if (!subjects.has(c)) err(n, `'${c}' is not in series.json's subjects (${[...subjects].sort().join(", ")})`, line.indexOf(c) + 1);
        }
      } else if (key === "size" && !SIZES.has(val.toLowerCase())) err(n, `size '${val}' must be one of ${JSON.stringify([...SIZES].sort())}`, col - 1);
      else if (key === "audio") {
        const a = /^([\d.]+)\s*-\s*([\d.]+)$/.exec(val);
        if (!a) err(n, "audio window must look like `3.10-7.40`", col - 1);
        else if (Number(a[2]) <= Number(a[1])) err(n, `audio window ends (${a[2]}) before it starts (${a[1]})`, col - 1);
      } else if (key === "dur" || key === "duration") {
        const v = val.toLowerCase();
        if (v !== "auto" && !v.startsWith("model")) {
          if (!Number.isFinite(Number(val)) || !val) err(n, `duration '${val}' is not a number, \`auto\` or \`model\``, col - 1);
          else if (shot) shot.dur = Number(val);
        }
      } else if (key === "pace" && !PACES.has(val.toLowerCase())) err(n, `pace '${val}' must be one of ${JSON.stringify([...PACES].sort())}`, col - 1);
      else if ((key === "first" || key === "last") && !KEYFRAMES.has(val.toLowerCase()) && !/\.(png|jpe?g|webp)$/i.test(val)) {
        err(n, `\`${key}: ${val}\` must be continuity, generate, import, none or a path to an image (.png, .jpg, .jpeg, .webp)`, col - 1);
      } else if (key === "plate" && val && !locations.has(val)) err(n, `plate '${val}' is not in series.json's locations`, col - 1);
      return;
    }
    if (!shot) return; // scene-setting prose under a sequence header
    shot.body = true;
  });
  closeShot();
  if (!header) errors.unshift({ file, line: 1, message: "script needs an episode header: `= ep01  Title`" });
  return { ok: errors.length === 0, errors, warnings, shots: errors.length ? [] : localShotSpans(text) };
}

/** A JSON.parse error's line and column (V8 says "position N", newer ones "line L column C"). */
export function jsonErrorAt(text: string, e: unknown): { line: number; col: number; message: string } {
  const msg = e instanceof Error ? e.message : String(e);
  const lc = /line (\d+) column (\d+)/.exec(msg);
  if (lc) return { line: Number(lc[1]), col: Number(lc[2]), message: msg.replace(/\s*\(line \d+ column \d+\)/, "") };
  const pos = /position (\d+)/.exec(msg);
  const p = pos ? Number(pos[1]) : text.length;
  const before = text.slice(0, p).split("\n");
  return { line: before.length, col: before[before.length - 1].length + 1, message: msg };
}

/** A series config: valid JSON with subjects and locations, then the script on disk against it. */
export function checkSeries(text: string, script: string): SourceCheck {
  let cfg: Partial<SeriesCfg>;
  try {
    cfg = JSON.parse(text) as Partial<SeriesCfg>;
  } catch (e) {
    const at = jsonErrorAt(text, e);
    return { ok: false, errors: [{ file: "series", line: at.line, col: at.col, message: at.message }], warnings: [], shots: [] };
  }
  const errors: SourceMessage[] = [];
  const lineOf = (key: string) => Math.max(1, text.split("\n").findIndex((l) => l.includes(`"${key}"`)) + 1);
  if (!cfg || typeof cfg !== "object" || Array.isArray(cfg)) {
    return { ok: false, errors: [{ file: "series", line: null, message: "series.json must be a JSON object" }], warnings: [], shots: [] };
  }
  if (!cfg.series || typeof cfg.series !== "object") errors.push({ file: "series", line: null, message: "series.json has no `series` block" });
  if (!cfg.subjects || typeof cfg.subjects !== "object") errors.push({ file: "series", line: null, message: "series.json has no `subjects` block" });
  for (const [id, v] of Object.entries(cfg.subjects ?? {})) {
    if (id.startsWith("_") || typeof v !== "object" || !v) continue;
    const kind = (v as { kind?: unknown }).kind;
    if (kind != null && !["character", "prop", "vehicle"].includes(String(kind))) {
      errors.push({ file: "series", line: lineOf(id), message: `subject '${id}': kind '${String(kind)}' must be character, prop or vehicle` });
    }
  }
  const withScript = checkScript(script, cfg, "script");
  return {
    ok: !errors.length && withScript.ok,
    errors: [...errors, ...withScript.errors],
    warnings: withScript.warnings,
    shots: withScript.shots,
  };
}

// ---------------------------------------------------------------------------
// promote
// ---------------------------------------------------------------------------

export interface PromoteInput {
  script: string;
  series: string;
  scriptName: string;
  /** each shot's override, by pass */
  shots: { shot: string; final: Override; proxy: Override }[];
  episodeTarget: string | null;
  refOverrides: { ref: string; view: string | null; subject: string | null; kind: string; values: Override }[];
  /** only this shot (GET /h3pipe/promote?shot=…) */
  only?: string | null;
}

/** What promoting an item takes out of the overrides. */
export interface PromoteDrop {
  scope: "shot" | "episode" | "ref";
  shot?: string;
  ref?: string;
  view?: string | null;
  field: string;
}

interface Planned extends PromoteItem {
  drop: PromoteDrop;
  scriptLine?: { shot: string; key: string; value: string };
  seriesSet?: { path: string[]; value: unknown };
}

const same = (a: unknown, b: unknown) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);

function loraLine(v: Lora[] | null | undefined): string | null {
  if (v == null || v.length === 0) return "none";
  if (v.length === 1 && (v[0].strength ?? 1) === 1) return v[0].name;
  return null;
}

/** Set `key: value` in a shot's own block: replace its line, else add after the last field. */
export function setShotLine(script: string, shot: string, key: string, value: string): { text: string; line: number } | null {
  const span = localShotSpans(script).find((s) => s.id === shot);
  if (!span) return null;
  const lines = script.split("\n");
  let lastKey = span.line; // 1-based
  for (let n = span.line + 1; n <= span.end_line; n++) {
    const m = /^([a-z_]+)\s*:/.exec(lines[n - 1]);
    if (!m || !META_KEYS.has(m[1])) continue;
    if (m[1] === key) {
      lines[n - 1] = `${key}: ${value}`;
      return { text: lines.join("\n"), line: n };
    }
    lastKey = n;
  }
  lines.splice(lastKey, 0, `${key}: ${value}`);
  return { text: lines.join("\n"), line: lastKey + 1 };
}

/** The promote plan, and a function that applies some of its items. */
export function planPromote(inp: PromoteInput): {
  items: PromoteItem[];
  left: PromoteLeft[];
  apply: (ids: string[] | "all") => { script: string; series: string; drops: PromoteDrop[]; promoted: string[] };
  diffs: { script: string; series: string };
} {
  const planned: Planned[] = [];
  const left: PromoteLeft[] = [];
  for (const s of inp.shots) {
    if (inp.only && s.shot !== inp.only) continue;
    const { final: f, proxy: p } = s;
    const shared = { ...p, ...f };
    const base = { scope: "shot" as const, shot: s.shot };
    const lineItem = (field: string, key: string, value: string, summary: string) => planned.push({
      id: `shot:${s.shot}:${field}`, ...base, field, value: shared[field as keyof Override], dest: "script", summary,
      drop: { ...base, field }, scriptLine: { shot: s.shot, key, value },
    });
    if (shared.target) lineItem("target", "target", shared.target, `${s.shot}: render on ${shared.target} (a target: line)`);
    for (const field of ["model", "loras", "steps"] as const) {
      const inF = field in f;
      const inP = field in p;
      if (!inF && !inP) continue;
      if (!(inF && inP) || !same(f[field], p[field])) {
        left.push({ ...base, field, reason: `set for the ${inF ? "final" : "proxy"} pass only${inF && inP ? " (the passes differ)" : ""}; a script \`${field === "loras" ? "lora" : field}:\` line applies to both passes` });
        continue;
      }
      if (field === "loras") {
        const v = loraLine(f.loras);
        if (v == null) left.push({ ...base, field, reason: "the script's `lora:` line takes one LoRA at strength 1; this override has several (or another strength)" });
        else lineItem(field, "lora", v, `${s.shot}: LoRA ${v}`);
      } else lineItem(field, field, String(f[field]), `${s.shot}: ${field} ${String(f[field])}`);
    }
    if (shared.prompt != null) left.push({ ...base, field: "prompt", reason: "compiled prompt text has no script form" });
    if (shared.seed != null) left.push({ ...base, field: "seed", reason: "a seed is kept by picking the take" });
    if (shared.negative != null) left.push({ ...base, field: "negative", reason: "a negative is per target and pass" });
    if (shared.note != null) left.push({ ...base, field: "note", reason: "a note isn't part of the script" });
    if (shared.model_low != null) left.push({ ...base, field: "model_low", reason: "the script has no line for a low-noise model" });
  }
  if (!inp.only) {
    if (inp.episodeTarget) {
      planned.push({
        id: "episode:target", scope: "episode", field: "target", value: inp.episodeTarget, dest: "series",
        summary: `The episode renders on ${inp.episodeTarget} (the series config's series.target)`,
        drop: { scope: "episode", field: "target" }, seriesSet: { path: ["series", "target"], value: inp.episodeTarget },
      });
    }
    for (const r of inp.refOverrides) {
      const base = { scope: "ref" as const, ref: r.ref, view: r.view };
      const m = /^(subject|location):(.+)$/.exec(r.ref);
      for (const [field, value] of Object.entries(r.values)) {
        if (value == null) continue;
        if (field === "target" && m && !r.view) {
          const block = m[1] === "subject" ? "subjects" : "locations";
          planned.push({
            id: `ref:${r.ref}:${field}`, ...base, field, value, dest: "series",
            summary: `${m[2]} generates with ${String(value)} (${block}.${m[2]}.target)`,
            drop: { ...base, field }, seriesSet: { path: [block, m[2], "target"], value },
          });
        } else {
          left.push({
            ...base, field,
            reason: field === "prompt" ? "a ref's prompt is compiled text; edit the design sentence in the series config instead"
              : field === "seed" ? "a seed is kept by picking the take"
              : !m ? "a keyframe has no entry in the series config" : `the series config has no ${field} for a ${r.kind}${r.view ? " view" : ""}`,
          });
        }
      }
    }
  }

  const apply = (ids: string[] | "all") => {
    const pick = ids === "all" ? planned : planned.filter((x) => ids.includes(x.id));
    let script = inp.script;
    const c = JSON.parse(inp.series) as Record<string, unknown>;
    for (const it of pick) {
      if (it.scriptLine) {
        const r = setShotLine(script, it.scriptLine.shot, it.scriptLine.key, it.scriptLine.value);
        if (r) script = r.text;
      }
      if (it.seriesSet) {
        let o = c;
        const path = it.seriesSet.path;
        for (const k of path.slice(0, -1)) {
          if (typeof o[k] !== "object" || o[k] == null) o[k] = {};
          o = o[k] as Record<string, unknown>;
        }
        o[path[path.length - 1]] = it.seriesSet.value;
      }
    }
    const series = pick.some((x) => x.seriesSet) ? JSON.stringify(c, null, 2) + "\n" : inp.series;
    return { script, series, drops: pick.map((x) => x.drop), promoted: pick.map((x) => x.id) };
  };

  // each script item's line number, as it lands
  for (const it of planned) {
    if (!it.scriptLine) continue;
    const r = setShotLine(inp.script, it.scriptLine.shot, it.scriptLine.key, it.scriptLine.value);
    if (r) it.line = r.line;
  }
  const all = apply("all");
  return {
    items: planned.map(({ drop: _d, scriptLine: _s, seriesSet: _ss, ...item }) => item),
    left,
    apply,
    diffs: {
      script: unifiedDiff(inp.script, all.script, `a/${inp.scriptName}`, `b/${inp.scriptName}`),
      series: unifiedDiff(inp.series, all.series, "a/series.json", "b/series.json"),
    },
  };
}
