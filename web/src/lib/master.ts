// A master being assembled, in words: what the Master dialog says under its
// progress bar, from the job's step and stage (h3master's events; the stages
// are h3assemble's and h3publish's progress lines). Pure functions.

import type { MasterJob, MasterTitles } from "../types";

const ASSEMBLE: Record<string, string> = {
  start: "Starting",
  probe: "Reading the clips",
  clips: "Writing the clips",
  join: "Joining the clips",
  prores: "Writing the ProRes .mov",
  verify: "Counting the master's frames",
};

const TITLES: Record<string, string> = {
  titles: "Encoding the",
  join: "Joining the titles on",
  verify: "Checking the joins",
  reencode: "Re-encoding the whole cut with its titles",
  prores: "Writing the ProRes .mov again",
};

/** "hh:mm" of an ISO time, or "" */
export function clock(iso: string | null | undefined): string {
  const m = /T(\d\d:\d\d)/.exec(iso ?? "");
  return m ? m[1] : "";
}

/** The running job's step in words: "Writing the clips 212/323 · sh2590". */
export function masterStageText(job: MasterJob): string {
  if (job.elsewhere) {
    const since = clock(job.started);
    return `Being assembled by ${job.by ?? "another run"}${since ? ` since ${since}` : ""}`;
  }
  const stage = job.stage ?? "";
  if (job.step === "titles") {
    if (stage === "titles") return `${TITLES.titles} ${job.text || "titles"}`;
    const words = TITLES[stage] ?? stage;
    const n = count(job);
    return n ? `${words} ${n}` : words;
  }
  const words = ASSEMBLE[stage] ?? stage;
  const n = count(job);
  const shot = (stage === "probe" || stage === "clips") && job.text ? ` · ${job.text}` : "";
  return `${words}${n ? ` ${n}` : ""}${shot}`;
}

function count(job: MasterJob): string {
  if (job.done == null || !job.total) return "";
  if (job.stage === "reencode") return `${Math.round((100 * job.done) / job.total)}%`;
  return `${job.done}/${job.total}`;
}

/** How far through the whole job, 0..1, or null when there's nothing to measure.
 * Assembling is most of it (clips 10-80%, reading them before that, counting
 * after); the titles are the last 15%. */
export function masterFraction(job: MasterJob): number | null {
  if (job.state !== "running" || job.elsewhere) return null;
  const part = job.done != null && job.total ? Math.min(1, job.done / job.total) : 0;
  if (job.step === "titles") {
    if (job.stage === "reencode") return 0.85 + 0.15 * part;
    return 0.85;
  }
  switch (job.stage) {
    case "probe": return 0.1 * part;
    case "clips": return 0.1 + 0.7 * part;
    case "join": return 0.8;
    case "prores":
    case "verify": return 0.82;
    default: return 0;
  }
}

/** How the titles went on, for the dialog and the toast: null when there were
 * none, or h3publish didn't say. */
export function pictureText(t: MasterTitles | null | undefined): string | null {
  if (!t?.picture) return null;
  if (t.picture === "copied") return "The cut's picture was copied as assembled; only the title clips were encoded.";
  return `The whole cut was re-encoded when the titles went on${t.why ? `, because ${t.why}` : ""}.`;
}
