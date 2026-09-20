// @vitest-environment happy-dom
//
// Windows that crash, and the boundary that contains them.
//
// The bug this file was written for: a take's sidecar `resolved.loras` has
// LISTS for want/using (targets/__init__.py; docs/API.md "Phase 9 as built"),
// the client's Resolution said `string`, and `shortName` did `name.split(...)`.
// Opening "Inspect shot" or "Show details" on such a take threw
// `TypeError: e.split is not a function` inside ResolvedNotes — and because the
// whole overlay (inspector, viewer, dialogs, context menu, toasts) was ONE
// React root with no error boundary, React unmounted all of it: the editor was
// dead until a page reload.
//
// So: real DOM (happy-dom), real roots, effects run, real mock API.
import { StrictMode, act, createElement, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Boundary } from "../src/components/ErrorBoundary";
import { setApi, setHost, type Host } from "../src/host";
import { shortName } from "../src/lib/format";
import { resolutionNotes, resolutionText } from "../src/lib/readiness";
import { createMockApi } from "../src/mock/mockApi";
import { Overlay } from "../src/surfaces";
import { detailKey, initialState, statusKey, store } from "../src/store";
import type { Resolution, ShotDetail } from "../src/types";

// The real shape, copied from a sidecar on disk
// (DeanStories/ep19/renders_proxy/sh010/sh010_t02.json).
const REAL_LORA_RESOLUTION: Resolution = {
  want: ["minimax_h3_ref2v_lightx2v_turbo_4step_v0.1_resized_avg_rank_20_bf16.safetensors"],
  using: ["minimax_h3_ref2v_lightx2v_turbo_4step_v0.1_resized_avg_rank_20_bf16.safetensors"],
  how: "exact",
} as Resolution;

describe("a resolution whose want/using are lists (the `loras` param)", () => {
  it("shortName takes a list, a number and a stray object", () => {
    expect(shortName(["a/b/one.safetensors", "two.safetensors"], 60)).toBe("one.safetensors, two.safetensors");
    expect(shortName([])).toBe("");
    expect(shortName(4)).toBe("4");
    expect(shortName(null)).toBe("");
  });

  it("resolutionText doesn't throw on the real sidecar shape", () => {
    // "exact" says nothing — but the old code built `want` before the switch
    expect(resolutionText("loras", REAL_LORA_RESOLUTION)).toBeNull();
    expect(resolutionNotes({ loras: REAL_LORA_RESOLUTION })).toEqual([]);
  });

  it("names every LoRA of a list when it has something to say", () => {
    expect(resolutionText("loras", { want: ["turbo.safetensors", "style.safetensors"], using: [], how: "base" }))
      .toBe("rendered with base settings: turbo.safetensors, style.safetensors missing");
    expect(resolutionText("loras", { want: ["turbo.safetensors"], using: ["turbo_v2.safetensors"], how: "family" }))
      .toBe("using turbo_v2.safetensors instead of turbo.safetensors (same family)");
    expect(resolutionText("loras", { want: [], using: [], how: "off" })).toBe("loras off: loras missing");
  });
});

// ---------------------------------------------------------------------------
// mounting for real: roots, effects, the store
// ---------------------------------------------------------------------------

const HOST: Host = { on: () => () => {}, toast: () => {}, show: () => {} };

let root: Root | null = null;
let el: HTMLElement | null = null;

function mount(node: ReactNode) {
  el = document.createElement("div");
  document.body.appendChild(el);
  const r = createRoot(el);
  root = r;
  act(() => r.render(createElement(StrictMode, null, node)));
  return el;
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  store.set(initialState());
  setHost(HOST);
});

afterEach(() => {
  if (root) act(() => root!.unmount());
  el?.remove();
  root = null;
  el = null;
  store.set(initialState());
  vi.restoreAllMocks();
});

/** The mock episode, with a take whose sidecar carries the real list shape. */
async function fillStore(): Promise<{ shot: string; take: number; pass: "proxy" }> {
  const api = createMockApi(() => {}, { latency: 0 });
  setApi(api);
  const ep = (await api.episodes())[0].ep;
  const pass = "proxy" as const;
  const st = await api.episode(ep, pass);
  const s = st.shots.find((x) => x.takes.length)!;
  const d: ShotDetail = await api.shot(ep, pass, s.shot);
  const t = d.takes[0];
  t.sidecar = { ...(t.sidecar ?? {}), resolved: { loras: REAL_LORA_RESOLUTION } };
  store.set({
    ep, pass, shot: s.shot, take: t.take,
    status: { [statusKey(ep, pass)]: st },
    details: { [detailKey(ep, pass, s.shot)]: d },
  });
  return { shot: s.shot, take: t.take, pass };
}

describe("the overlay against a real sidecar", () => {
  it("opens the inspector on a take whose resolved.loras are lists", async () => {
    const { shot } = await fillStore();
    store.set({ inspector: true });
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    const host = mount(createElement(Overlay));
    await act(async () => { await Promise.resolve(); });
    expect(host.querySelector(".h3-inspector")).toBeTruthy();
    expect(host.querySelector(".h3-crash")).toBeNull();
    expect(host.textContent).toContain(shot);
    expect(errors).not.toHaveBeenCalled();
  });

  it("opens Show details on the same take", async () => {
    const { shot, take, pass } = await fillStore();
    store.set({ sidecar: { shot, take, pass } });
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    const host = mount(createElement(Overlay));
    await act(async () => { await Promise.resolve(); });
    expect(host.textContent).toContain("Copy JSON");
    expect(host.querySelector(".h3-crash")).toBeNull();
    expect(errors).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// the boundary itself
// ---------------------------------------------------------------------------

function Boom(): never {
  throw new Error("boom: e.split is not a function");
}

describe("the error boundary", () => {
  it("shows a card for the broken window and leaves its siblings alone", () => {
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    const host = mount(createElement("div", null,
      createElement(Boundary, { name: "Inspector" }, createElement(Boom)),
      createElement(Boundary, { name: "Context menu" }, createElement("button", { className: "menu-item" }, "Inspect shot")),
    ));
    const card = host.querySelector(".h3-crash");
    expect(card).toBeTruthy();
    expect(card!.textContent).toContain("Inspector stopped");
    expect(card!.textContent).toContain("boom");
    // the sibling window is untouched: the ⋯ menu keeps working
    expect(host.querySelector(".menu-item")).toBeTruthy();
    // and it logged the error with its component stack
    expect(errors.mock.calls.some((c) => String(c[0]).includes("Inspector crashed"))).toBe(true);
  });

  it("copies the error, and can be dismissed", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(globalThis.navigator, "clipboard", { value: { writeText }, configurable: true });
    vi.spyOn(console, "error").mockImplementation(() => {});
    const host = mount(createElement(Boundary, { name: "Take details" }, createElement(Boom)));
    const [copy] = [...host.querySelectorAll("button")].filter((b) => b.textContent?.includes("Copy error"));
    await act(async () => { copy.click(); await Promise.resolve(); });
    expect(writeText).toHaveBeenCalled();
    expect(String(writeText.mock.calls[0][0])).toContain("Take details crashed");
    expect(String(writeText.mock.calls[0][0])).toContain("Component stack");
    const close = [...host.querySelectorAll("button")].find((b) => b.title?.startsWith("Hide this"))!;
    act(() => close.click());
    expect(host.querySelector(".h3-crash")).toBeNull();
  });
});
