// Phase 9a: the Script and Series config windows. Each edits one authored file
// in CodeMirror: live checking (lint), Save (Ctrl+S) with a rebuild, and edits
// made in another editor noticed on focus and every few seconds. The Script
// window also follows the selected shot, and selects the shot the cursor is in.

import { useEffect, useMemo, useRef, useState } from "react";
import { afterAuthoredWrite, closeSource, openPromote, openSource, select, setSourceDirty, showInScript } from "../actions";
import { api, host } from "../host";
import { checkCounts, listedMessages, shotAtLine, spansFor, toDiagnostics } from "../lib/source";
import { SourceSession, type SessionState } from "../lib/sourceSession";
import { store, useApp, useSelector } from "../store";
import type { SourceFile, SourceSaveResult } from "../types";
import { CodeEditor, type CodeEditorHandle } from "./CodeEditor";
import { FloatingWindow, type Rect } from "./FloatingWindow";

/** how often an open window looks at the disk (plus on window focus) */
export const POLL_MS = 5000;
/** the cursor rests this long in a shot before that shot is selected */
const CURSOR_SELECT_MS = 350;

const LABEL: Record<SourceFile, string> = { script: "Script", series: "Series config" };
const ICON: Record<SourceFile, string> = { script: "pi pi-file-edit", series: "pi pi-book" };

function defaultRect(file: SourceFile): () => Rect {
  return () => {
    const W = window.innerWidth || 1280;
    const H = window.innerHeight || 800;
    const w = Math.max(360, Math.min(680, Math.round(W * 0.42)));
    const off = file === "series" ? 40 : 0;
    // right of ComfyUI's sidebar (where the Shots tab is), so the bin stays usable
    const x = Math.max(16, Math.min(420, W - w - 16)) + off;
    return { x, y: 56 + off, w, h: Math.max(320, H - 56 - 60 - off) };
  };
}

function epName(ep: string): string {
  return ep.replace(/[\\/]+$/, "").split(/[\\/]/).pop() ?? ep;
}

/** Both windows (each renders nothing while closed). */
export function SourceWindows() {
  return (
    <>
      <SourceWindow file="script" />
      <SourceWindow file="series" />
    </>
  );
}

function SourceWindow({ file }: { file: SourceFile }) {
  const open = useApp((s) => s.sourceOpen[file]);
  const ep = useApp((s) => s.ep);
  if (!open || !ep) return null;
  return <SourceWindowBody file={file} storeEp={ep} />;
}

function SourceWindowBody({ file, storeEp }: { file: SourceFile; storeEp: string }) {
  // the episode this window edits: it follows the open episode unless it has unsaved edits
  const [ep, setEp] = useState(storeEp);
  const [session, setSession] = useState<SourceSession | null>(null);
  const editor = useRef<CodeEditorHandle>(null);

  useEffect(() => {
    const s = new SourceSession(api(), ep, file, {
      replace: (text, why) => editor.current?.replace(text, { history: why !== "load" }),
      saved: (r: SourceSaveResult) => onSaved(file, s, r),
    });
    setSession(s);
    void s.load();
    return () => s.dispose();
  }, [ep, file]);

  const dirty = useApp((s) => s.sourceDirty[file]);
  useEffect(() => {
    if (storeEp !== ep && !dirty) setEp(storeEp);
  }, [storeEp, ep, dirty]);

  if (!session || session.ep !== ep) return null;
  return <SessionView key={`${ep}|${file}`} session={session} file={file} ep={ep} storeEp={storeEp} editor={editor} follow={() => setEp(storeEp)} />;
}

function onSaved(file: SourceFile, s: SourceSession, r: SourceSaveResult) {
  const name = s.get().path || LABEL[file];
  const errs = r.check?.errors?.length ?? 0;
  if (!r.build) host().toast(errs ? "warn" : "success", `Saved ${name}`, errs ? `${errs} error(s): the build didn't run` : undefined);
  else if (r.build.ok) host().toast("success", `Saved ${name} and rebuilt`);
  afterAuthoredWrite(r.build ?? null);
}

function useSession<T>(s: SourceSession, sel: (x: SessionState) => T): T {
  return useSelector(s.state, sel);
}

function SessionView({ session, file, ep, storeEp, editor, follow }: {
  session: SourceSession; file: SourceFile; ep: string; storeEp: string;
  editor: React.RefObject<CodeEditorHandle>; follow: () => void;
}) {
  const loading = useSession(session, (s) => s.loading);
  const loadError = useSession(session, (s) => s.loadError);
  const baseHash = useSession(session, (s) => s.baseHash);
  const path = useSession(session, (s) => s.path);
  const text = useSession(session, (s) => s.text);
  const dirty = useSession(session, (s) => s.dirty);
  const check = useSession(session, (s) => s.check);
  const checking = useSession(session, (s) => s.checking);
  const checkError = useSession(session, (s) => s.checkError);
  const conflict = useSession(session, (s) => s.conflict);
  const saving = useSession(session, (s) => s.saving);
  const saved = useSession(session, (s) => s.saved);
  const saveError = useSession(session, (s) => s.saveError);
  const shot = useApp((s) => s.shot);
  const focus = useApp((s) => s.scriptFocus);
  const sourceN = useApp((s) => s.sourceN);
  const isScript = file === "script";
  const name = path || (isScript ? "script" : "series.json");
  const ready = baseHash != null;

  useEffect(() => {
    setSourceDirty(file, dirty);
  }, [file, dirty]);
  useEffect(() => () => setSourceDirty(file, false), [file]);

  // --- outside edits: poll on focus, every few seconds, and after the editor wrote a file
  useEffect(() => {
    const poll = () => void session.poll();
    const vis = () => document.visibilityState === "visible" && poll();
    const t = setInterval(poll, POLL_MS);
    window.addEventListener("focus", poll);
    document.addEventListener("visibilitychange", vis);
    return () => {
      clearInterval(t);
      window.removeEventListener("focus", poll);
      document.removeEventListener("visibilitychange", vis);
    };
  }, [session]);
  const lastN = useRef(sourceN);
  useEffect(() => {
    if (sourceN === lastN.current) return;
    lastN.current = sourceN;
    // the other file may have changed (a script is checked against the series config)
    void session.poll().then((r) => r !== "reloaded" && session.scheduleCheck(0));
  }, [sourceN, session]);

  // --- lint
  useEffect(() => {
    const view = editor.current?.view();
    if (!view || !check) return;
    editor.current!.setDiagnostics(toDiagnostics(view.state.doc, check.result, file, path));
  }, [check, file, path, editor, ready]);

  // --- shot sync (script only)
  const spans = useMemo(() => (isScript ? spansFor(text, check) : []), [isScript, text, check]);
  const fromEditor = useRef<string | null>(null);
  const shownShot = useRef<string | null>(null);
  useEffect(() => {
    if (!isScript || !ready) return;
    const sp = shot ? spans.find((x) => x.id === shot) : undefined;
    editor.current?.highlight(sp ? { from: sp.line, to: sp.end_line } : null);
    if (sp && shot !== shownShot.current && fromEditor.current !== shot) editor.current?.reveal({ from: sp.line, to: sp.end_line }, { ifHidden: true });
    shownShot.current = shot;
  }, [isScript, ready, shot, spans, editor]);
  const lastFocus = useRef<number>(0);
  useEffect(() => {
    if (!isScript || !ready || !focus || focus.n === lastFocus.current) return;
    lastFocus.current = focus.n;
    const sp = spans.find((x) => x.id === focus.shot);
    if (!sp) {
      host().toast("info", `${focus.shot} isn't in ${name}`, "The build's shot may be gone from the script (an orphan), or the script doesn't parse here.");
      return;
    }
    fromEditor.current = null;
    editor.current?.highlight({ from: sp.line, to: sp.end_line });
    editor.current?.reveal({ from: sp.line, to: sp.end_line }, { y: "start" });
  }, [isScript, ready, focus, spans, editor, name]);
  const cursorTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (cursorTimer.current) clearTimeout(cursorTimer.current);
  }, []);
  const onCursorLine = (line: number) => {
    if (!isScript) return;
    if (cursorTimer.current) clearTimeout(cursorTimer.current);
    cursorTimer.current = setTimeout(() => {
      cursorTimer.current = null;
      if (ep !== store.get().ep) return; // another episode's script
      const id = shotAtLine(spansFor(session.get().text, session.get().check), line);
      if (id && id !== store.get().shot) {
        fromEditor.current = id;
        select(id, null);
      }
    }, CURSOR_SELECT_MS);
  };

  const save = () => {
    if (!ready || saving) return;
    if (!session.get().dirty && !conflict) return;
    void session.save();
  };
  const close = () => {
    if (session.get().dirty && !confirm(`Discard your unsaved edits to ${name}?`)) return;
    closeSource(file);
  };

  const counts = checkCounts(check?.result ?? null);
  const nErr = check?.result.errors?.length ?? 0;
  const listed = listedMessages(check?.result ?? null, file, path);
  const behind = ep !== storeEp;

  const head = (
    <>
      <i className={`${ICON[file]} h3-muted`} />
      <b>{LABEL[file]}</b>
      <span className="h3-mono h3-small h3-muted h3-ell" style={{ maxWidth: 200 }} title={`${ep} · ${name}`}>{name}</span>
      {dirty && <span className="h3-dirty" title="Unsaved edits (Ctrl+S saves)">●</span>}
      {checking && <i className="pi pi-spin pi-spinner h3-muted" style={{ fontSize: 10 }} title="Checking…" />}
      {counts ? (
        <button
          className={`h3-chip ${nErr ? "h3-chip-err" : "h3-chip-warn"}`}
          title="The check's messages (open the list)"
          onClick={() => editor.current?.openLintPanel()}
        >
          {counts}
        </button>
      ) : check && !checking ? <span className="h3-chip h3-chip-ok" title="The check found nothing">✓</span> : null}
      <span className="h3-grow" />
      {isScript && (
        <button className="h3-btn" title="Move the episode's overrides that the script or series config can express into them" onClick={() => openPromote(null)}>
          Promote…
        </button>
      )}
      <button className="h3-btn h3-primary" disabled={!ready || saving || (!dirty && !conflict)} title="Save and rebuild (Ctrl+S)" onClick={save}>
        <i className={saving ? "pi pi-spin pi-spinner" : "pi pi-save"} /> {saving ? "Saving…" : "Save"}
      </button>
      <button className="h3-btn h3-icon" title="Close" onClick={close}><i className="pi pi-times" /></button>
    </>
  );

  return (
    <FloatingWindow storageKey={`h3pipe.win.source.${file}`} defaultRect={defaultRect(file)} head={head} className="h3-source-win" minW={340} minH={240}>
      <div
        className="h3-source-body"
        onKeyDownCapture={(e) => {
          if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === "s") {
            e.preventDefault();
            e.stopPropagation();
            save();
          }
        }}
      >
        {behind && (
          <div className="h3-note h3-banner">
            This is {epName(ep)}'s {isScript ? "script" : "series config"}, not the open episode's ({epName(storeEp)}).
            <span className="h3-row" style={{ marginTop: 4 }}>
              <button className="h3-btn" disabled={saving} onClick={save}>Save</button>
              <button className="h3-btn" onClick={() => (!dirty || confirm(`Discard your unsaved edits to ${name}?`)) && follow()}>
                {dirty ? "Discard and follow" : "Follow"}
              </button>
            </span>
          </div>
        )}
        {conflict && (
          <div className="h3-note h3-note-warn h3-banner" role="alert">
            <b>{conflict.reason === "save" ? `Not saved: ${name} changed on disk.` : `${name} changed on disk.`}</b>{" "}
            {conflict.reason === "save"
              ? "It was edited outside ComfyUI after you opened it."
              : "It was edited outside ComfyUI, and you have unsaved edits here."}
            <span className="h3-row" style={{ marginTop: 4 }}>
              <button className="h3-btn" title="Load the file as it is on disk; your edits here are dropped" onClick={() => session.reload()}>
                Reload
              </button>
              <button
                className="h3-btn"
                title={conflict.reason === "save" ? "Save your version over the one on disk" : "Keep editing your version; saving it overwrites the one on disk"}
                onClick={() => void session.keepMine()}
              >
                Keep mine{conflict.reason === "save" ? " (overwrite)" : ""}
              </button>
            </span>
          </div>
        )}
        {saved && <SavedNote s={saved} onClose={() => session.dismissSaved()} />}
        {saveError && (
          <div className="h3-note h3-note-err h3-banner">
            <div className="h3-row"><b className="h3-grow">Not saved</b><button className="h3-link" onClick={() => session.dismissSaved()}>✕</button></div>
            <div className="h3-pre-wrap">{saveError}</div>
          </div>
        )}
        {checkError && <div className="h3-note h3-banner h3-small">Can't check: {checkError}</div>}
        {loadError && (
          <div className="h3-pad h3-col">
            <div className="h3-note h3-note-err">Can't open the {LABEL[file].toLowerCase()}: {loadError}</div>
            <div className="h3-row"><button className="h3-btn" onClick={() => void session.load()}>Retry</button></div>
          </div>
        )}
        {!ready && loading && <div className="h3-empty-state">Loading…</div>}
        {ready && (
          <CodeEditor
            ref={editor}
            language={isScript ? "script" : "json"}
            initial={session.get().text}
            onChange={(t) => session.edit(t)}
            onCursorLine={onCursorLine}
            onSave={save}
          />
        )}
        {listed.length > 0 && (
          <div className="h3-source-others">
            {listed.slice(0, 20).map((m, i) => {
              const where = m.own ? LABEL[file] : m.file === "script" ? "Script" : m.file === "series" ? "Series config" : m.file;
              return (
                <button
                  key={i}
                  className={`h3-source-msg h3-${m.severity}`}
                  title={m.own ? m.message : `Open the ${where.toLowerCase()}`}
                  onClick={() => {
                    if (m.own) return;
                    if (isScript) openSource("series");
                    else {
                      openSource("script");
                      // a line in the script on disk: show the shot it's in
                      void api().source(ep, "script").then((d) => {
                        const id = m.line != null ? shotAtLine(d.shots.length ? d.shots : spansFor(d.text, null), m.line) : null;
                        if (id) showInScript(id);
                      }).catch(() => {});
                    }
                  }}
                >
                  <span className="h3-muted">{where}{m.line != null ? `, line ${m.line}` : ""}:</span> {m.message}
                </button>
              );
            })}
          </div>
        )}
      </div>
    </FloatingWindow>
  );
}

function SavedNote({ s, onClose }: { s: NonNullable<SessionState["saved"]>; onClose: () => void }) {
  const failed = s.built === false;
  const err = failed
    ? (["final", "proxy"] as const).map((p) => s.build?.passes[p]).find((x) => x && !x.ok)?.error ?? ""
    : "";
  return (
    <div className={`h3-note h3-banner ${failed ? "h3-note-err" : "h3-note-info"}`}>
      <div className="h3-row">
        <span className="h3-grow">
          {s.built === true && "Saved and rebuilt."}
          {failed && <b>Saved; the build failed.</b>}
          {s.built === null && (s.errors ? `Saved with ${s.errors} error${s.errors > 1 ? "s" : ""}; not rebuilt.` : "Saved.")}
        </span>
        <button className="h3-link" onClick={onClose}>✕</button>
      </div>
      {err && <pre className="h3-pre h3-err" style={{ maxHeight: 120 }}>{err}</pre>}
    </div>
  );
}
