// Phase 8.6: files into ref slots. Drop an image (or, on a voice, an audio file)
// onto a ref, a character's view or a keyframe, or pick one with the file
// picker: it uploads (multipart POST /h3pipe/refs/import, pick=1) and goes live.

import { useRef, useState, type DragEvent, type ReactNode } from "react";
import { dismissUpload, uploadRef } from "../actions";
import { ACCEPT, dragHasFiles, uploadText } from "../lib/lookback";
import { uploadKey, useApp } from "../store";
import { Progress } from "./Thumb";

export type SlotKind = "image" | "audio";

/** The upload in flight (or failed) for a slot. */
export function useUpload(ref: string, view: string | null) {
  return useApp((s) => s.uploads[uploadKey(ref, view)]);
}

/**
 * A drop target for one slot. `refuse` (a text) turns a drop into that
 * message instead (e.g. a character's row: drop onto a view). Children render
 * inside; the slot's upload progress / error shows below them.
 */
export function DropSlot({ refId, view, kind, refuse, className, children, title, status = true, dataRef }: {
  refId: string;
  view: string | null;
  kind: SlotKind;
  refuse?: string | null;
  className?: string;
  children: ReactNode;
  title?: string;
  /** show the progress / error line (off when the caller shows it elsewhere) */
  status?: boolean;
  /** a data-ref attribute (the Refs tab scrolls to it) */
  dataRef?: string;
}) {
  const [over, setOver] = useState(false);
  const depth = useRef(0);
  const enter = (e: DragEvent) => {
    if (!dragHasFiles(e.dataTransfer)) return;
    e.preventDefault();
    e.stopPropagation();
    depth.current++;
    setOver(true);
  };
  const leave = (e: DragEvent) => {
    if (!dragHasFiles(e.dataTransfer)) return;
    e.stopPropagation();
    depth.current = Math.max(0, depth.current - 1);
    if (!depth.current) setOver(false);
  };
  const overFn = (e: DragEvent) => {
    if (!dragHasFiles(e.dataTransfer)) return;
    e.preventDefault();
    e.stopPropagation();
    e.dataTransfer.dropEffect = refuse ? "none" : "copy";
  };
  const drop = (e: DragEvent) => {
    if (!dragHasFiles(e.dataTransfer)) return;
    e.preventDefault();
    e.stopPropagation();
    depth.current = 0;
    setOver(false);
    if (refuse) return;
    const f = e.dataTransfer.files?.[0];
    if (f) void uploadRef(refId, view, f);
  };
  return (
    <div
      className={`h3-drop${over ? (refuse ? " h3-drop-no" : " h3-drop-over") : ""}${className ? ` ${className}` : ""}`}
      onDragEnter={enter}
      onDragLeave={leave}
      onDragOver={overFn}
      onDrop={drop}
      title={title}
      data-drop={refuse ? undefined : kind}
      data-ref={dataRef}
    >
      {children}
      {over && <div className="h3-drop-hint">{refuse || `Drop ${kind === "audio" ? "an audio file" : "an image"}: it goes live`}</div>}
      {status && <UploadStatus refId={refId} view={view} />}
    </div>
  );
}

/** The slot's upload: a progress bar, or the error with Dismiss. */
export function UploadStatus({ refId, view }: { refId: string; view: string | null }) {
  const u = useUpload(refId, view);
  if (!u) return null;
  if (u.error) {
    return (
      <div className="h3-note h3-note-err h3-small h3-upload-err" onClick={(e) => e.stopPropagation()}>
        {u.error} <button className="h3-link" onClick={() => dismissUpload(refId, view)}>Dismiss</button>
      </div>
    );
  }
  return (
    <div className="h3-col h3-upload" style={{ gap: 1 }} title={`Uploading ${u.name}`}>
      <span className="h3-small h3-muted h3-ell">uploading {u.name} · {uploadText(u)}</span>
      <Progress value={u.sent} max={u.total || 1} />
    </div>
  );
}

/** "Upload…": the file picker, for when dragging isn't handy. */
export function UploadButton({ refId, view, kind, label = "Upload…", disabled, title, icon = true }: {
  refId: string;
  view: string | null;
  kind: SlotKind;
  label?: string;
  disabled?: boolean;
  title?: string;
  icon?: boolean;
}) {
  const input = useRef<HTMLInputElement>(null);
  const u = useUpload(refId, view);
  const busy = !!u && !u.error;
  return (
    <>
      <button
        className="h3-btn"
        disabled={disabled || busy}
        title={title ?? `Upload ${kind === "audio" ? "an audio file" : "an image"} from this computer as a new candidate, live at once (or drop one here)`}
        onClick={(e) => {
          e.stopPropagation();
          input.current?.click();
        }}
      >
        {icon && <i className={busy ? "pi pi-spin pi-spinner" : "pi pi-upload"} />} {label}
      </button>
      <input
        ref={input}
        type="file"
        accept={ACCEPT[kind]}
        style={{ display: "none" }}
        onClick={(e) => e.stopPropagation()}
        onChange={(e) => {
          const f = e.target.files?.[0];
          e.target.value = ""; // the same file can be picked again
          if (f) void uploadRef(refId, view, f);
        }}
      />
    </>
  );
}
