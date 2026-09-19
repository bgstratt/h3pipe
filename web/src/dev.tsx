// The dev page (dev.html): every surface side by side on the mock API.
// Not part of the ComfyUI bundle (that's main.ts).

import { pushToast, start } from "./actions";
import { setApi, setHost, type Host, type HostEvent, type Surface } from "./host";
import { createMockApi } from "./mock/mockApi";
import { mountOverlay, mountSurface } from "./surfaces";

const listeners = new Map<HostEvent, Set<(d: unknown) => void>>();

function emit(event: HostEvent, detail: unknown) {
  // like ComfyUI's websocket: asynchronous, after the HTTP response
  setTimeout(() => listeners.get(event)?.forEach((cb) => cb(detail)), 0);
}

const devHost: Host = {
  on(event, cb) {
    if (!listeners.has(event)) listeners.set(event, new Set());
    listeners.get(event)!.add(cb);
    return () => listeners.get(event)?.delete(cb);
  },
  toast(severity, summary, detail) {
    pushToast({ severity, summary, detail });
  },
  show(surface: Surface) {
    const el = document.getElementById(`p-${surface}`);
    if (!el) return;
    el.scrollIntoView({ block: "nearest" });
    el.classList.add("flash");
    setTimeout(() => el.classList.remove("flash"), 600);
  },
};

const params = new URLSearchParams(location.search);
setHost(devHost);
setApi(createMockApi(emit, { firstRun: params.has("firstrun") }));

for (const s of ["shots", "refs", "timeline"] as Surface[]) {
  mountSurface(s, document.getElementById(s)!);
}
mountOverlay({ toasts: true });
void start();

document.getElementById("theme")?.addEventListener("click", () => document.documentElement.classList.toggle("light"));
