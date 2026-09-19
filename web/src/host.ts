// What the editor needs from whatever hosts it. comfy.ts provides the ComfyUI
// implementation; the dev page provides a standalone one on the mock API.
// Components only ever talk to `api()` and `host()`, never to ComfyUI directly.

import type { Api } from "./api";

export type HostEvent =
  | "progress"
  | "executing"
  | "execution_start"
  | "execution_success"
  | "execution_error"
  | "execution_interrupted"
  | "h3pipe.take"
  | "h3pipe.episode"
  | "h3pipe.ref";

export type Severity = "success" | "info" | "warn" | "error";
/** Docked surfaces (sidebar tabs and the bottom panel). The inspector and the
 * viewer are floating windows in the overlay, opened through actions. */
export type Surface = "shots" | "refs" | "timeline";

export interface Host {
  /** Subscribe to a websocket event; the callback gets the event's `detail`. */
  on(event: HostEvent, cb: (detail: unknown) => void): () => void;
  toast(severity: Severity, summary: string, detail?: string): void;
  /** Bring a surface into view (open its sidebar tab / bottom panel). */
  show(surface: Surface): void;
}

const noopHost: Host = {
  on: () => () => {},
  toast: (sev, summary, detail) => console.log(`[h3pipe ${sev}] ${summary}`, detail ?? ""),
  show: () => {},
};

let currentHost: Host = noopHost;
let currentApi: Api | null = null;

export function setHost(h: Host) {
  currentHost = h;
}

export function host(): Host {
  return currentHost;
}

export function setApi(a: Api) {
  currentApi = a;
}

export function api(): Api {
  if (!currentApi) throw new Error("h3pipe: API not installed");
  return currentApi;
}
