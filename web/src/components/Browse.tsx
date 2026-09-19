// The folder browser (GET /h3pipe/browse): choosing project roots, opening an
// episode, and picking a file on the ComfyUI machine to import as a ref.

import { useCallback, useEffect, useState } from "react";
import { addRoot, closeBrowse, importRef, openEpisodeAt, refLabel, removeRoot } from "../actions";
import { errText } from "../api";
import { api } from "../host";
import { AUDIO_EXT, IMAGE_EXT, crumbs } from "../lib/browse";
import { viewLabel } from "../lib/refs";
import { useApp, type BrowseState } from "../store";
import type { BrowseDir, BrowseFile, BrowseResult } from "../types";
import { Dialog } from "./Dialogs";

const LAST_KEY = "h3pipe.browse.last";

function lastPath(purpose: string): string | null {
  try {
    return localStorage.getItem(`${LAST_KEY}.${purpose}`);
  } catch {
    return null;
  }
}

function rememberPath(purpose: string, path: string) {
  try {
    if (path) localStorage.setItem(`${LAST_KEY}.${purpose}`, path);
  } catch {
    /* ignore */
  }
}

export function BrowseDialog() {
  const b = useApp((s) => s.browse);
  if (!b) return null;
  return <BrowseBody key={`${b.purpose}|${b.ref ?? ""}|${b.view ?? ""}`} b={b} />;
}

type Sel = { kind: "dir"; d: BrowseDir } | { kind: "file"; f: BrowseFile } | null;

function BrowseBody({ b }: { b: BrowseState }) {
  const config = useApp((s) => s.config);
  const busyRoots = useApp((s) => !!s.busy.config);
  const busyImport = useApp((s) => !!b.ref && !!s.busy[`refimport|${b.ref}`]);
  const [res, setRes] = useState<BrowseResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [sel, setSel] = useState<Sel>(null);
  const [typed, setTyped] = useState("");
  const importing = b.purpose === "import";
  const roots = config?.roots ?? [];

  const go = useCallback(async (path: string | null) => {
    setLoading(true);
    setErr(null);
    try {
      const r = await api().browse(path, importing ? b.files ?? "image" : null);
      setRes(r);
      setSel(null);
      rememberPath(b.purpose, r.path);
    } catch (e) {
      setErr(errText(e));
    } finally {
      setLoading(false);
    }
  }, [b.purpose, b.files, importing]);

  useEffect(() => {
    void go(lastPath(b.purpose) ?? roots[0] ?? null);
    // only on open
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const openEpisode = async (d: { path: string }, parent: string | null) => {
    if (await openEpisodeAt(d.path, parent)) closeBrowse();
  };
  const doImport = async (path: string) => {
    if (!b.ref) return;
    if (await importRef(b.ref, b.view ?? null, path)) closeBrowse();
  };

  const here = res?.path ?? "";
  const filesListed = res?.files !== undefined;
  const ext = b.files === "audio" ? AUDIO_EXT : IMAGE_EXT;
  const files = (res?.files ?? []).filter((f) => ext.test(f.name));
  const selDir = sel?.kind === "dir" ? sel.d : null;
  const selFile = sel?.kind === "file" ? sel.f : null;
  // "Open episode" works on the selected folder, else the one we're in
  const epTarget = selDir?.episode ? { path: selDir.path, parent: here || null } : res?.episode ? { path: here, parent: res.parent || null } : null;
  const rootTarget = selDir?.path ?? (here || null);

  const title = importing
    ? <>Import into {refLabel(b.ref ?? "", b.view)} {b.view ? <span className="h3-muted h3-small">{viewLabel(b.view)}</span> : null}</>
    : "Project folders";

  return (
    <Dialog
      title={title}
      onClose={closeBrowse}
      wide
      footer={
        <>
          {importing ? (
            <>
              <span className="h3-muted h3-small h3-grow h3-ell">{selFile ? selFile.path : "Pick a file"}</span>
              <button className="h3-btn" onClick={closeBrowse}>Cancel</button>
              <button className="h3-btn h3-primary" disabled={!selFile || busyImport} onClick={() => selFile && void doImport(selFile.path)}>
                <i className={busyImport ? "pi pi-spin pi-spinner" : "pi pi-download"} /> Import
              </button>
            </>
          ) : (
            <>
              <span className="h3-muted h3-small h3-grow">Double-click an episode to open it.</span>
              <button className="h3-btn" disabled={!rootTarget || busyRoots} title={rootTarget ? `Add ${rootTarget} as a project root` : "Browse into a folder first"} onClick={() => rootTarget && void addRoot(rootTarget)}>
                <i className="pi pi-plus" /> Add as root
              </button>
              <button className="h3-btn h3-primary" disabled={!epTarget || busyRoots} title={epTarget ? `Open ${epTarget.path}` : "Select an episode folder"} onClick={() => epTarget && void openEpisode(epTarget, epTarget.parent)}>
                <i className="pi pi-folder-open" /> Open episode
              </button>
              <button className="h3-btn" onClick={closeBrowse}>Close</button>
            </>
          )}
        </>
      }
    >
      <div className="h3-row h3-browse-bar">
        <button className="h3-btn h3-icon" title="Up" disabled={!res || res.parent === null || loading} onClick={() => void go(res?.parent || null)}>
          <i className="pi pi-arrow-up" />
        </button>
        <div className="h3-crumbs h3-grow">
          <button className="h3-link" onClick={() => void go(null)} title="Home and drives">
            <i className="pi pi-desktop" />
          </button>
          {crumbs(here).map((c, i) => (
            <span key={c.path + i}>
              {i > 0 && <span className="h3-muted"> › </span>}
              <button className="h3-link" onClick={() => void go(c.path)}>{c.label}</button>
            </span>
          ))}
        </div>
        {loading && <i className="pi pi-spin pi-spinner h3-muted" />}
        <button className="h3-btn h3-icon" title="Refresh" onClick={() => void go(here || null)}><i className="pi pi-refresh" /></button>
      </div>
      {err && <div className="h3-note h3-note-err">{err}</div>}
      <div className="h3-browse-list" role="listbox">
        {res?.dirs.map((d) => (
          <div
            key={d.path}
            role="option"
            aria-selected={selDir?.path === d.path}
            className={`h3-browse-item${selDir?.path === d.path ? " h3-sel" : ""}${d.episode ? " h3-episode" : ""}`}
            onClick={() => setSel({ kind: "dir", d })}
            onDoubleClick={() => (d.episode && !importing ? void openEpisode(d, here || null) : void go(d.path))}
            title={d.episode && !importing ? `${d.path}\nDouble-click to open this episode` : d.path}
          >
            <i className={d.episode ? "pi pi-video" : "pi pi-folder"} />
            <span className="h3-grow h3-ell">{d.name}</span>
            {d.episode && <span className="h3-badge h3-b-cut">episode</span>}
            {!d.episode && d.series_config && <span className="h3-badge">series</span>}
            {roots.some((r) => r.replace(/[\\/]+$/, "").toLowerCase() === d.path.replace(/[\\/]+$/, "").toLowerCase()) && <span className="h3-badge h3-b-ok">root</span>}
          </div>
        ))}
        {importing && files.map((f) => (
          <div
            key={f.path}
            role="option"
            aria-selected={selFile?.path === f.path}
            className={`h3-browse-item${selFile?.path === f.path ? " h3-sel" : ""}`}
            onClick={() => setSel({ kind: "file", f })}
            onDoubleClick={() => void doImport(f.path)}
            title={f.path}
          >
            <i className={b.files === "audio" ? "pi pi-volume-up" : "pi pi-image"} />
            <span className="h3-grow h3-ell">{f.name}</span>
            {f.size != null && <span className="h3-muted h3-small">{Math.max(1, Math.round(f.size / 1024))} KB</span>}
          </div>
        ))}
        {res && !res.dirs.length && !(importing && files.length) && <div className="h3-empty-state">Empty folder.</div>}
        {res?.truncated && <div className="h3-muted h3-small h3-pad">The list was cut short: this folder holds a lot.</div>}
      </div>
      {importing && res && !filesListed && (
        <div className="h3-note">
          This server's folder browser lists folders only, not files. Type the file's full path below.
        </div>
      )}
      <div className="h3-row">
        <input
          className="h3-in h3-grow h3-mono"
          placeholder={importing ? "or type a file path on the ComfyUI machine" : "or type a folder path"}
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && typed.trim() && !importing) void go(typed.trim());
          }}
        />
        {!importing && <button className="h3-btn" disabled={!typed.trim()} onClick={() => void go(typed.trim())}>Go</button>}
        {!importing && (
          <button className="h3-btn" disabled={!typed.trim() || busyRoots} onClick={() => void addRoot(typed.trim())} title="Add the typed folder as a root without browsing to it">
            Add typed path
          </button>
        )}
        {importing && (
          <button className="h3-btn" disabled={!typed.trim() || busyImport} onClick={() => void doImport(typed.trim())}>Import typed path</button>
        )}
      </div>
      {!importing && (
        <div className="h3-col" style={{ gap: 2 }}>
          <div className="h3-h">Project roots</div>
          {!roots.length && <div className="h3-muted h3-small">None yet. Browse to the folder that holds your shows (or one show), then Add as root.</div>}
          {roots.map((r) => (
            <div key={r} className="h3-row h3-small">
              <i className="pi pi-folder h3-muted" />
              <button className="h3-link h3-grow h3-ell" style={{ textAlign: "left" }} title="Browse here" onClick={() => void go(r)}>{r}</button>
              <button className="h3-btn h3-icon" title="Remove this root" disabled={busyRoots} onClick={() => confirm(`Stop looking for episodes in ${r}?`) && void removeRoot(r)}>✕</button>
            </div>
          ))}
          <div className="h3-muted h3-small">Episodes are found up to two levels below a root.</div>
        </div>
      )}
    </Dialog>
  );
}
