// Syntax highlighting for the episode script (epNN.md), as a CodeMirror
// StreamLanguage. The format is docs/AUTHORING.md; the rules below mirror the
// parser (h3core/story.py) line for line, so what's coloured as dialogue or a
// field is what the build reads as one:
//
//   = ep05  Title                episode header
//   # sq01  workshop_wide        sequence: id, location
//   ## sh010                     shot
//   who: dean, bolt              a field (the parser's META_KEYS), lower-case key
//   NARRATOR (V.O., into phone): a line      dialogue: ALL-CAPS name, (parenthetical)
//   // a comment                 ignored (the whole line)
//   anything else                action prose
//
// Headers, fields and dialogue only count from the first column (the parser
// doesn't strip the left of a line); a comment may be indented.

import { StreamLanguage, type StreamParser, type StringStream } from "@codemirror/language";
import { tags as t, type Tag } from "@lezer/highlight";

/** h3core/story.py META_KEYS */
export const META_KEYS = new Set([
  "who", "cast", "with", "props", "size", "audio", "dur", "duration", "camera", "sound", "music", "policy", "continuous", "text",
  "pace", "plate", "retention", "model", "lora", "steps", "extras", "target", "profile", "first", "last",
]);

/** fields whose value is a list of series config subject ids */
const NAME_KEYS = new Set(["who", "cast", "with", "props"]);
/** fields whose value is prose (a sentence for the prompt) */
const PROSE_KEYS = new Set(["camera", "sound", "music", "extras", "text"]);

/** A dialogue parenthetical's voice markers (story.py VO_TOKENS / OS_TOKENS). */
const VOICE_TOKENS = new Set(["vo", "voiceover", "voover", "os", "offscreen"]);

/** The token names this language emits (the keys of TOKEN_TABLE). */
export type ScriptToken =
  | "comment" | "mark" | "episodeId" | "episodeTitle" | "sequenceId" | "location" | "shotId" | "headerRest"
  | "key" | "unknownKey" | "punct" | "names" | "value" | "number" | "prose"
  | "speaker" | "paren" | "voice" | "dialogue";

export interface Segment {
  /** length in characters */
  len: number;
  /** null: plain text (action prose, spaces) */
  tok: ScriptToken | null;
}

// The parser's dialogue pattern: NAME, optional (parenthetical), colon, a line.
const DIALOGUE = /^([A-Z][A-Z0-9_ '\-]*?)(\s*)(?:(\()([^)]*)(\)))?(\s*:\s*)(.+)$/;
const META = /^([a-z_]+)(\s*:\s*)(.*)$/;

/** Split `s` into runs: non-space runs get `tok`, whitespace stays plain. */
function words(s: string, tok: ScriptToken | null): Segment[] {
  const out: Segment[] = [];
  for (const m of s.match(/\s+|[^\s]+/g) ?? []) out.push({ len: m.length, tok: /^\s/.test(m) ? null : tok });
  return out;
}

function seg(len: number, tok: ScriptToken | null): Segment[] {
  return len > 0 ? [{ len, tok }] : [];
}

/** A field's value: names, a number, a word, or prose, by key. */
function metaValue(key: string, v: string): Segment[] {
  if (!v) return [];
  if (NAME_KEYS.has(key)) {
    // names and the commas between them
    const out: Segment[] = [];
    for (const m of v.match(/,|\s+|[^,\s]+/g) ?? []) out.push({ len: m.length, tok: m === "," ? "punct" : /^\s/.test(m) ? null : "names" });
    return out;
  }
  if (PROSE_KEYS.has(key)) return seg(v.length, "prose");
  // `audio: 3.10-7.40`, `dur: 3.04`, `steps: 8`, `dur: model 3-8`
  const out: Segment[] = [];
  for (const m of v.match(/\d+(?:\.\d+)?|[^\d]+/g) ?? []) out.push({ len: m.length, tok: /^\d/.test(m) ? "number" : /^\s+$/.test(m) ? null : "value" });
  return out;
}

/** A dialogue parenthetical's inside: voice markers (V.O., O.S.) stand out. */
function parenthetical(inner: string): Segment[] {
  const out: Segment[] = [];
  for (const m of inner.match(/,|[^,]+/g) ?? []) {
    if (m === ",") {
      out.push({ len: 1, tok: "paren" });
      continue;
    }
    const lead = m.length - m.trimStart().length;
    const body = m.trim();
    const trail = m.length - lead - body.length;
    const voice = VOICE_TOKENS.has(body.toLowerCase().replace(/[^a-z]/g, ""));
    out.push(...seg(lead, "paren"), ...seg(body.length, voice ? "voice" : "paren"), ...seg(trail, "paren"));
  }
  return out;
}

/** How one line of a script reads, as coloured runs covering the whole line. */
export function classifyLine(line: string): Segment[] {
  const text = line.replace(/\s+$/, "");
  const tail = seg(line.length - text.length, null);
  if (!text.trim()) return seg(line.length, null);
  if (text.trim().startsWith("//")) return [{ len: line.length, tok: "comment" }];

  if (text.startsWith("= ")) {
    const m = /^(=)(\s+)(\S*)(\s*)(.*)$/.exec(text)!;
    return [...seg(1, "mark"), ...seg(m[2].length, null), ...seg(m[3].length, "episodeId"), ...seg(m[4].length, null),
      ...seg(m[5].length, "episodeTitle"), ...tail];
  }
  if (text.startsWith("## ")) {
    const m = /^(##)(\s+)(\S*)(.*)$/.exec(text)!;
    return [...seg(2, "mark"), ...seg(m[2].length, null), ...seg(m[3].length, "shotId"), ...seg(m[4].length, "headerRest"), ...tail];
  }
  if (text.startsWith("# ")) {
    const m = /^(#)(\s+)(\S*)(\s*)(\S*)(.*)$/.exec(text)!;
    return [...seg(1, "mark"), ...seg(m[2].length, null), ...seg(m[3].length, "sequenceId"), ...seg(m[4].length, null),
      ...seg(m[5].length, "location"), ...seg(m[6].length, "headerRest"), ...tail];
  }

  const d = DIALOGUE.exec(text);
  if (d) {
    const [, name, sp, open, inner, , colon, said] = d;
    return [
      ...seg(name.length, "speaker"), ...seg(sp.length, null),
      ...(open ? [...seg(1, "paren"), ...parenthetical(inner), ...seg(1, "paren")] : []),
      ...seg(colon.length, "punct"), ...seg(said.length, "dialogue"), ...tail,
    ];
  }

  const k = META.exec(text);
  if (k) {
    const [, key, colon, value] = k;
    if (META_KEYS.has(key)) {
      return [...seg(key.length, "key"), ...seg(colon.length, "punct"), ...metaValue(key, value), ...tail];
    }
    // a lower-case `word:` that isn't a field: the parser reads it as action,
    // which is usually a typo (`sise: wide`)
    return [...seg(key.length, "unknownKey"), ...seg(colon.length, "punct"), ...words(value, null), ...tail];
  }
  return seg(line.length, null);
}

interface State {
  /** the rest of the current line's runs */
  queue: Segment[];
}

/** The StreamParser (exported for tests: feed it a StringStream per line). */
export const scriptParser: StreamParser<State> = {
  name: "h3script",
  startState: () => ({ queue: [] }),
  copyState: (s) => ({ queue: s.queue.slice() }),
  token(stream: StringStream, state: State): string | null {
    if (stream.sol()) state.queue = classifyLine(stream.string);
    const next = state.queue.shift();
    if (!next || next.len <= 0) {
      stream.skipToEnd();
      return null;
    }
    for (let i = 0; i < next.len && !stream.eol(); i++) stream.next();
    return next.tok;
  },
  blankLine: (state) => {
    state.queue = [];
  },
  languageData: { commentTokens: { line: "//" } },
  tokenTable: TOKEN_TABLE(),
};

/** Token names → highlight tags. */
function TOKEN_TABLE(): Record<ScriptToken, Tag> {
  return {
    comment: t.lineComment,
    mark: t.processingInstruction,
    episodeId: t.special(t.heading),
    episodeTitle: t.heading1,
    sequenceId: t.heading2,
    location: t.typeName,
    shotId: t.heading3,
    headerRest: t.meta,
    key: t.propertyName,
    unknownKey: t.invalid,
    punct: t.punctuation,
    names: t.variableName,
    value: t.atom,
    number: t.number,
    prose: t.string,
    speaker: t.className,
    paren: t.annotation,
    voice: t.modifier,
    dialogue: t.quote,
  };
}

export const scriptLanguage = StreamLanguage.define(scriptParser);
