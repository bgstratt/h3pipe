// ALL ComfyUI-specific glue lives here: the extension registration, the HTTP
// transport, websocket events, toasts and showing panels. Checked against
// ComfyUI frontend 1.52.7:
//  - app.registerExtension({name, setup, bottomPanelTabs}); bottomPanelTabs are
//    registered at registerExtension time via useBottomPanelStore
//    .registerExtensionBottomPanelTabs (targetPanel defaults to "terminal").
//  - app.extensionManager is the workspace pinia store: registerSidebarTab(tab),
//    toast.add({severity, summary, detail, life}), sidebarTab.{activeSidebarTabId,
//    toggleSidebarTab(id)}, bottomPanel.{activeBottomPanelTabId, bottomPanelVisible,
//    toggleBottomPanelTab(id)}. Pinia unwraps refs, so these are plain values.
//  - Custom tabs are rendered by ExtensionSlot: render(el) from a Vue ref
//    callback (can repeat), destroy() from onBeforeUnmount.
//  - api.addEventListener(type, fn) registers custom message types too; the
//    event's `detail` is the message's data. "executing" carries only the node id.
//  - api.fetchApi(path) prefixes /api; api.apiURL(path) gives the same URL.

import { app as comfyApp } from "comfyui/app";
import { api as comfyApi } from "comfyui/api";
import { start } from "./actions";
import { createHttpApi } from "./api";
import { setApi, setHost, type Host, type HostEvent, type Surface } from "./host";
import { mountOverlay, mountSurface, unmountSurface } from "./surfaces";

interface CustomTab {
  id: string;
  title: string;
  type: "custom";
  render: (el: HTMLElement) => void;
  destroy?: () => void;
}

interface SidebarTab extends CustomTab {
  icon: string;
  tooltip?: string;
  label?: string;
}

interface ComfyApp {
  registerExtension(ext: { name: string; setup?: () => void | Promise<void>; bottomPanelTabs?: CustomTab[] }): void;
  extensionManager?: {
    registerSidebarTab?(tab: SidebarTab): void;
    toast?: { add(msg: { severity: string; summary: string; detail?: string; life?: number }): void };
    sidebarTab?: { activeSidebarTabId?: string | null; toggleSidebarTab?(id: string): void };
    bottomPanel?: {
      activeBottomPanelTabId?: string | null;
      bottomPanelVisible?: boolean;
      toggleBottomPanelTab?(id: string): void;
    };
    command?: { execute?(id: string): unknown };
  };
}

interface ComfyApi {
  fetchApi(path: string, init?: RequestInit): Promise<Response>;
  apiURL(path: string): string;
  addEventListener(type: string, fn: (e: CustomEvent) => void): void;
  removeEventListener(type: string, fn: (e: CustomEvent) => void): void;
}

const app = comfyApp as ComfyApp;
const api = comfyApi as ComfyApi;

// Round 2: the Queue tab is gone (ComfyUI's own queue is enough) and the
// inspector is a floating window in the overlay, not a sidebar tab.
const IDS: Record<Surface, string> = {
  shots: "h3pipe-shots",
  refs: "h3pipe-refs",
  timeline: "h3pipe-timeline",
};

const comfyHost: Host = {
  on(event: HostEvent, cb) {
    const fn = (e: CustomEvent) => cb(e.detail);
    api.addEventListener(event, fn);
    return () => api.removeEventListener(event, fn);
  },
  toast(severity, summary, detail) {
    const t = app.extensionManager?.toast;
    if (t?.add) t.add({ severity, summary, detail, life: severity === "error" ? 10000 : 4000 });
    else console[severity === "error" ? "error" : "log"](`[h3pipe] ${summary}`, detail ?? "");
  },
  show(surface) {
    const em = app.extensionManager;
    const id = IDS[surface];
    try {
      if (surface === "timeline") {
        const bp = em?.bottomPanel;
        if (bp && !(bp.bottomPanelVisible && bp.activeBottomPanelTabId === id)) bp.toggleBottomPanelTab?.(id);
        return;
      }
      const sb = em?.sidebarTab;
      if (sb && sb.activeSidebarTabId !== id) sb.toggleSidebarTab?.(id);
      else if (!sb) em?.command?.execute?.(`Workspace.ToggleSidebarTab.${id}`);
    } catch (e) {
      console.warn("[h3pipe] couldn't open", surface, e);
    }
  },
};

function tab(surface: Surface, title: string): CustomTab {
  return {
    id: IDS[surface],
    title,
    type: "custom",
    render: (el) => mountSurface(surface, el),
    destroy: () => unmountSurface(surface),
  };
}

export function install() {
  setHost(comfyHost);
  setApi(createHttpApi({ fetch: (p, init) => api.fetchApi(p, init), url: (p) => api.apiURL(p) }));

  app.registerExtension({
    name: "h3pipe.editor",
    bottomPanelTabs: [tab("timeline", "h3 Timeline")],
    async setup() {
      const em = app.extensionManager;
      if (!em?.registerSidebarTab) {
        console.error("[h3pipe] this ComfyUI frontend has no extensionManager.registerSidebarTab; the editor needs frontend >= 1.3");
        return;
      }
      em.registerSidebarTab({ ...tab("shots", "h3 Shots"), icon: "pi pi-video", tooltip: "h3pipe: episode, shots and takes" });
      em.registerSidebarTab({ ...tab("refs", "h3 Refs"), icon: "pi pi-palette", tooltip: "h3pipe: references (characters, props, locations, voices)" });
      mountOverlay();
      await start();
    },
  });
}
