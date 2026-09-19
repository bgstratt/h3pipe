// Phase 8.6 (docs/API.md "look-back"): discarding takes and candidates,
// Generate missing's result, and files dropped or picked for a ref slot.
// Pure functions; actions and components call them.

import type { Pass, RefGenerateMissingResult } from "../types";
import { tn } from "./format";

/** The confirmation before discarding a video take. */
export function discardTakeText(shot: string, take: number, pass: Pass, folder: string | null | undefined, picked: boolean): string {
  const trash = `${folder || (pass === "proxy" ? "renders_proxy" : "renders")}/_trash/${shot}/`;
  return `Discard ${shot} ${tn(take)} (${pass})?\n\nIts video, sidecar and pictures move to ${trash} (nothing is deleted: move them back by hand to undo).`
    + (picked ? `\n\nThe ${pass} cut picks this take: it goes back to the latest usable take.` : "");
}

/** The confirmation before discarding a ref candidate. */
export function discardRefText(label: string, take: number, live: boolean, kind: "ref" | "keyframe" = "ref"): string {
  return `Discard ${label} ${tn(take)}?\n\nIt moves to refs/_takes/_trash/ (nothing is deleted: move it back by hand to undo).`
    + (live
      ? kind === "keyframe"
        ? "\n\nIt is the live keyframe: the keyframe is cleared, as Clear does."
        : "\n\nIt is the live file: the ref is cleared (as Unpick does) until you pick another candidate."
      : "");
}

const IMAGE_EXT = /\.(png|jpe?g|webp)$/i;
const AUDIO_EXT = /\.(wav|mp3|flac|ogg|m4a)$/i;

/** The file types an import takes (API.md: images png/jpg/jpeg/webp, audio wav/mp3/flac/ogg/m4a). */
export const ACCEPT: Record<"image" | "audio", string> = {
  image: ".png,.jpg,.jpeg,.webp,image/png,image/jpeg,image/webp",
  audio: ".wav,.mp3,.flac,.ogg,.m4a,audio/*",
};

/** Why a file can't go into a slot of this kind, or "" when it can. */
export function fileKindProblem(f: { name: string; type?: string }, want: "image" | "audio"): string {
  const ok = want === "image" ? IMAGE_EXT.test(f.name) : AUDIO_EXT.test(f.name);
  if (ok) return "";
  return want === "image"
    ? `${f.name} isn't an image this ref can use (png, jpg or webp).`
    : `${f.name} isn't an audio file a voice can use (wav, mp3, flac, ogg or m4a).`;
}

/** Whether a drag carries files (not text or a link being dragged). */
export function dragHasFiles(dt: { types?: ArrayLike<string> | readonly string[] } | null | undefined): boolean {
  return !!dt?.types && Array.from(dt.types as ArrayLike<string>).includes("Files");
}

/** "37%" / "1.2 of 3.4 MB" for an upload in flight. */
export function uploadText(u: { sent: number; total: number }): string {
  if (u.total > 0) return `${Math.min(100, Math.round((u.sent / u.total) * 100))}%`;
  return u.sent ? `${(u.sent / 1024 / 1024).toFixed(1)} MB` : "…";
}

/** A toast for Generate missing's answer: counts, then what each group was. */
export function missingResultText(r: Pick<RefGenerateMissingResult, "queued" | "picked" | "skipped">): { summary: string; detail: string } {
  const q = r.queued.length;
  const p = r.picked.length;
  const s = r.skipped.length;
  // a continuity frame may be listed as queued or as picked (see api.ts)
  const cont = (x: { method?: string }[]) => x.filter((y) => y.method === "continuity").length;
  const qc = cont(r.queued);
  const pc = cont(r.picked);
  const parts = [
    q ? `queued ${q}${qc ? ` (${qc} from neighbouring takes)` : ""}` : "",
    p ? `picked ${p}${pc ? ` (${pc} from neighbouring takes)` : ""}` : "",
    s ? `skipped ${s}` : "",
  ].filter(Boolean);
  const summary = parts.length ? `Generate missing: ${parts.join(", ")}` : "Generate missing: nothing to do";
  const detail = [
    q ? "Queued candidates go live as they finish." : "",
    ...r.skipped.slice(0, 8).map((x) => `skipped ${x.ref}${x.view ? ` (${x.view})` : ""}: ${x.reason}`),
    s > 8 ? `… and ${s - 8} more skipped` : "",
  ].filter(Boolean).join("\n");
  return { summary, detail };
}
