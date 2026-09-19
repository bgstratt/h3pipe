// A CodeMirror 6 editor for the authored files: the script (its own
// StreamLanguage, src/lib/scriptLang.ts) or the series config (JSON). Themed
// from the editor's CSS variables, which follow ComfyUI's palette.

import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
import { json } from "@codemirror/lang-json";
import {
  bracketMatching, foldGutter, foldKeymap, HighlightStyle, indentOnInput, LanguageSupport, syntaxHighlighting,
} from "@codemirror/language";
import { lintGutter, lintKeymap, openLintPanel, setDiagnostics, type Diagnostic } from "@codemirror/lint";
import { highlightSelectionMatches, search, searchKeymap } from "@codemirror/search";
import { EditorState, StateEffect, StateField, Transaction, type Extension, type Range } from "@codemirror/state";
import {
  Decoration, drawSelection, EditorView, highlightActiveLine, highlightActiveLineGutter, keymap, lineNumbers, type DecorationSet,
} from "@codemirror/view";
import { tags as t } from "@lezer/highlight";
import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";
import { scriptLanguage } from "../lib/scriptLang";
import type { Diag } from "../lib/source";

// ---------------------------------------------------------------------------
// theme
// ---------------------------------------------------------------------------

const theme = EditorView.theme({
  "&": { height: "100%", backgroundColor: "var(--h3-bg)", color: "var(--h3-fg)", fontSize: "12px" },
  "&.cm-focused": { outline: "none" },
  ".cm-scroller": { fontFamily: "var(--h3-mono)", lineHeight: "1.5" },
  ".cm-content": { caretColor: "var(--h3-fg)", paddingBottom: "40vh" },
  ".cm-cursor, .cm-dropCursor": { borderLeftColor: "var(--h3-fg)" },
  "&.cm-focused .cm-selectionBackground, .cm-selectionBackground, ::selection": {
    backgroundColor: "color-mix(in srgb, var(--h3-accent) 32%, transparent) !important",
  },
  ".cm-gutters": { backgroundColor: "var(--h3-bg3)", color: "var(--h3-muted)", borderRight: "1px solid var(--h3-border)" },
  ".cm-activeLine": { backgroundColor: "color-mix(in srgb, var(--h3-fg) 5%, transparent)" },
  ".cm-activeLineGutter": { backgroundColor: "color-mix(in srgb, var(--h3-fg) 9%, transparent)", color: "var(--h3-fg)" },
  ".cm-selectionMatch": { backgroundColor: "color-mix(in srgb, var(--h3-accent) 16%, transparent)" },
  ".cm-matchingBracket": { backgroundColor: "color-mix(in srgb, var(--h3-accent) 25%, transparent)", outline: "none" },
  ".cm-searchMatch": { backgroundColor: "color-mix(in srgb, var(--h3-queued) 30%, transparent)" },
  ".cm-searchMatch.cm-searchMatch-selected": { backgroundColor: "color-mix(in srgb, var(--h3-queued) 55%, transparent)" },
  ".cm-panels": { backgroundColor: "var(--h3-bg3)", color: "var(--h3-fg)" },
  ".cm-panels.cm-panels-bottom": { borderTop: "1px solid var(--h3-border)" },
  ".cm-panels.cm-panels-top": { borderBottom: "1px solid var(--h3-border)" },
  ".cm-panel input, .cm-panel button, .cm-textfield": {
    backgroundColor: "var(--h3-bg2)", color: "var(--h3-fg)", border: "1px solid var(--h3-border)", borderRadius: "3px",
  },
  ".cm-button": { backgroundImage: "none" },
  ".cm-tooltip": { backgroundColor: "var(--h3-bg3)", color: "var(--h3-fg)", border: "1px solid var(--h3-border)" },
  ".cm-diagnostic": { fontFamily: "inherit" },
  ".cm-diagnostic-error": { borderLeftColor: "var(--h3-failed)" },
  ".cm-diagnostic-warning": { borderLeftColor: "var(--h3-stale)" },
  ".cm-lintRange-error": { backgroundImage: "none", textDecoration: "underline wavy var(--h3-failed)", textUnderlineOffset: "3px" },
  ".cm-lintRange-warning": { backgroundImage: "none", textDecoration: "underline wavy var(--h3-stale)", textUnderlineOffset: "3px" },
  ".cm-panel.cm-panel-lint ul [aria-selected]": { backgroundColor: "color-mix(in srgb, var(--h3-accent) 25%, transparent)" },
  ".cm-foldPlaceholder": { backgroundColor: "var(--h3-bg2)", border: "1px solid var(--h3-border)", color: "var(--h3-muted)" },
  ".h3-cm-shot": { backgroundColor: "color-mix(in srgb, var(--h3-accent) 11%, transparent)" },
  ".h3-cm-shot-first": { boxShadow: "inset 0 1px 0 color-mix(in srgb, var(--h3-accent) 55%, transparent)" },
  ".h3-cm-shot-last": { boxShadow: "inset 0 -1px 0 color-mix(in srgb, var(--h3-accent) 55%, transparent)" },
  ".h3-cm-shot-first.h3-cm-shot-last": {
    boxShadow: "inset 0 1px 0 color-mix(in srgb, var(--h3-accent) 55%, transparent), inset 0 -1px 0 color-mix(in srgb, var(--h3-accent) 55%, transparent)",
  },
});

/** Colours are CSS variables (styles.css), mixed from ComfyUI's foreground so
 * they read on light and dark palettes alike. */
export const h3Highlight = HighlightStyle.define([
  // the script
  { tag: t.special(t.heading), color: "var(--h3-syn-head)", fontWeight: "700" },
  { tag: t.heading1, color: "var(--h3-syn-head)", fontWeight: "700" },
  { tag: t.heading2, color: "var(--h3-syn-seq)", fontWeight: "700" },
  { tag: t.heading3, color: "var(--h3-syn-shot)", fontWeight: "700" },
  { tag: t.typeName, color: "var(--h3-syn-loc)" },
  { tag: t.processingInstruction, color: "var(--h3-muted)" },
  { tag: t.meta, color: "var(--h3-muted)" },
  { tag: t.className, color: "var(--h3-syn-speaker)", fontWeight: "700" },
  { tag: t.annotation, color: "var(--h3-muted)", fontStyle: "italic" },
  { tag: t.modifier, color: "var(--h3-syn-voice)", fontStyle: "italic" },
  { tag: t.quote, color: "var(--h3-syn-dialogue)" },
  { tag: t.variableName, color: "var(--h3-syn-name)" },
  { tag: t.invalid, textDecoration: "underline dotted var(--h3-stale)", color: "var(--h3-stale)" },
  // both
  { tag: t.propertyName, color: "var(--h3-syn-key)" },
  { tag: [t.lineComment, t.comment], color: "var(--h3-muted)", fontStyle: "italic" },
  { tag: t.string, color: "var(--h3-syn-string)" },
  { tag: [t.number, t.atom, t.bool, t.null], color: "var(--h3-syn-num)" },
  { tag: [t.punctuation, t.separator, t.brace, t.squareBracket], color: "var(--h3-muted)" },
]);

// ---------------------------------------------------------------------------
// the highlighted shot (a line decoration)
// ---------------------------------------------------------------------------

export interface LineRange {
  /** 1-based, inclusive */
  from: number;
  to: number;
}

const setShotLines = StateEffect.define<LineRange | null>();

const shotLines = StateField.define<{ range: LineRange | null; deco: DecorationSet }>({
  create: () => ({ range: null, deco: Decoration.none }),
  update(v, tr) {
    let range = v.range;
    for (const e of tr.effects) if (e.is(setShotLines)) range = e.value;
    if (range === v.range && !tr.docChanged) return v;
    if (!range) return { range, deco: Decoration.none };
    const doc = tr.state.doc;
    const a = Math.min(Math.max(1, range.from), doc.lines);
    const b = Math.min(Math.max(a, range.to), doc.lines);
    const decos: Range<Decoration>[] = [];
    for (let n = a; n <= b; n++) {
      const cls = ["h3-cm-shot", n === a ? "h3-cm-shot-first" : "", n === b ? "h3-cm-shot-last" : ""].filter(Boolean).join(" ");
      decos.push(Decoration.line({ class: cls }).range(doc.line(n).from));
    }
    return { range, deco: Decoration.set(decos) };
  },
  provide: (f) => EditorView.decorations.from(f, (v) => v.deco),
});

// ---------------------------------------------------------------------------
// the component
// ---------------------------------------------------------------------------

export type EditorLanguage = "script" | "json";

export interface CodeEditorHandle {
  /** Replace the text, touching only what changed so the cursor and scroll
   * stay put (`history: false`: the change can't be undone, e.g. a load). */
  replace(text: string, opts?: { history?: boolean }): void;
  /** Scroll a line range's first line into view: to the top ("start", the
   * default), or only as far as needed ("nearest"). */
  reveal(range: LineRange, opts?: { select?: boolean; y?: "start" | "nearest" | "center"; ifHidden?: boolean }): void;
  /** Show a shot's lines highlighted (null: none). */
  highlight(range: LineRange | null): void;
  setDiagnostics(d: Diag[]): void;
  openLintPanel(): void;
  focus(): void;
  view(): EditorView | null;
}

interface Props {
  language: EditorLanguage;
  initial: string;
  onChange?: (text: string) => void;
  /** the cursor moved to `line` (1-based) because the user moved it (click, keys, typing) */
  onCursorLine?: (line: number) => void;
  onSave?: () => void;
  readOnly?: boolean;
}

/** The smallest single change that turns `a` into `b`. */
export function minimalChange(a: string, b: string): { from: number; to: number; insert: string } | null {
  if (a === b) return null;
  let pre = 0;
  const max = Math.min(a.length, b.length);
  while (pre < max && a.charCodeAt(pre) === b.charCodeAt(pre)) pre++;
  let suf = 0;
  while (suf < max - pre && a.charCodeAt(a.length - 1 - suf) === b.charCodeAt(b.length - 1 - suf)) suf++;
  return { from: pre, to: a.length - suf, insert: b.slice(pre, b.length - suf) };
}

function languageExt(lang: EditorLanguage): Extension {
  return lang === "json" ? [json(), foldGutter(), bracketMatching(), indentOnInput()] : new LanguageSupport(scriptLanguage);
}

export const CodeEditor = forwardRef<CodeEditorHandle, Props>(function CodeEditor(
  { language, initial, onChange, onCursorLine, onSave, readOnly },
  ref,
) {
  const host = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);
  // the latest callbacks, without rebuilding the editor
  const cb = useRef({ onChange, onCursorLine, onSave });
  cb.current = { onChange, onCursorLine, onSave };

  useEffect(() => {
    const el = host.current;
    if (!el) return;
    const view = new EditorView({
      parent: el,
      state: EditorState.create({
        doc: initial,
        extensions: [
          lineNumbers(),
          highlightActiveLineGutter(),
          history(),
          drawSelection(),
          highlightActiveLine(),
          highlightSelectionMatches(),
          search({ top: true }),
          lintGutter(),
          EditorView.lineWrapping,
          EditorState.readOnly.of(!!readOnly),
          languageExt(language),
          syntaxHighlighting(h3Highlight),
          theme,
          shotLines,
          keymap.of([
            { key: "Mod-s", preventDefault: true, run: () => (cb.current.onSave?.(), true) },
            ...defaultKeymap, ...historyKeymap, ...searchKeymap, ...lintKeymap, ...foldKeymap, indentWithTab,
          ]),
          EditorView.updateListener.of((u) => {
            if (u.docChanged) cb.current.onChange?.(u.state.doc.toString());
            if (u.selectionSet && u.view.hasFocus && u.transactions.some((tr) => tr.isUserEvent("select") || tr.isUserEvent("input") || tr.isUserEvent("delete"))) {
              cb.current.onCursorLine?.(u.state.doc.lineAt(u.state.selection.main.head).number);
            }
          }),
        ],
      }),
    });
    viewRef.current = view;
    return () => {
      view.destroy();
      viewRef.current = null;
    };
    // the editor is built once per language; `initial` is only the first text
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [language, readOnly]);

  useImperativeHandle(ref, () => ({
    replace(text, opts) {
      const view = viewRef.current;
      if (!view) return;
      const ch = minimalChange(view.state.doc.toString(), text);
      if (!ch) return;
      view.dispatch({ changes: ch, annotations: opts?.history === false ? Transaction.addToHistory.of(false) : [] });
    },
    reveal(range, opts) {
      const view = viewRef.current;
      if (!view) return;
      const doc = view.state.doc;
      const a = doc.line(Math.min(Math.max(1, range.from), doc.lines));
      if (opts?.ifHidden) {
        const top = view.lineBlockAt(a.from).top;
        const s = view.scrollDOM;
        if (top >= s.scrollTop && top <= s.scrollTop + s.clientHeight - 24) return;
      }
      view.dispatch({
        selection: opts?.select ? { anchor: a.from } : undefined,
        effects: EditorView.scrollIntoView(a.from, { y: opts?.y ?? "start", yMargin: 48 }),
      });
    },
    highlight(range) {
      viewRef.current?.dispatch({ effects: setShotLines.of(range) });
    },
    setDiagnostics(d) {
      const view = viewRef.current;
      if (!view) return;
      const len = view.state.doc.length;
      const diags: Diagnostic[] = d.map((x) => ({ ...x, from: Math.min(x.from, len), to: Math.min(Math.max(x.to, x.from), len) }));
      view.dispatch(setDiagnostics(view.state, diags));
    },
    openLintPanel() {
      const view = viewRef.current;
      if (view) openLintPanel(view);
    },
    focus() {
      viewRef.current?.focus();
    },
    view: () => viewRef.current,
  }), []);

  return (
    <div
      ref={host}
      className="h3-code"
      // keys typed here are the editor's, not ComfyUI's graph shortcuts
      onKeyDown={(e) => e.stopPropagation()}
      onKeyUp={(e) => e.stopPropagation()}
    />
  );
});
