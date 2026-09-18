import { describe, expect, it, vi } from "vitest";
import {
  PREFS_KEY, ZOOM_DEFAULT, ZOOM_MAX, clampZoom, createStore, initialState, loadPrefs, persistPrefs,
  promptOf, renderingTakes, savePrefs,
} from "../src/store";

function memStorage(init: Record<string, string> = {}) {
  const m = new Map(Object.entries(init));
  return {
    getItem: (k: string) => m.get(k) ?? null,
    setItem: vi.fn((k: string, v: string) => void m.set(k, v)),
    map: m,
  };
}

describe("createStore", () => {
  it("merges patches and notifies subscribers", () => {
    const st = createStore({ a: 1, b: "x" });
    const fn = vi.fn();
    const off = st.subscribe(fn);
    st.set({ a: 2 });
    expect(st.get()).toEqual({ a: 2, b: "x" });
    st.set((s) => ({ b: s.b + "y" }));
    expect(st.get().b).toBe("xy");
    expect(fn).toHaveBeenCalledTimes(2);
    off();
    st.set({ a: 3 });
    expect(fn).toHaveBeenCalledTimes(2);
  });

  it("does nothing (keeps identity, no notify) when nothing changed", () => {
    const st = createStore({ a: 1, o: { k: 1 } });
    const before = st.get();
    const fn = vi.fn();
    st.subscribe(fn);
    st.set({ a: 1, o: before.o });
    expect(st.get()).toBe(before);
    expect(fn).not.toHaveBeenCalled();
  });
});

describe("prefs (last episode and pass)", () => {
  it("round-trips through storage", () => {
    const s = memStorage();
    savePrefs({ ep: "C:\\Shows\\ep05", pass: "final", zoom: 60 }, s);
    expect(loadPrefs(s)).toEqual({ ep: "C:\\Shows\\ep05", pass: "final", zoom: 60 });
    expect(JSON.parse(s.map.get(PREFS_KEY)!)).toEqual({ ep: "C:\\Shows\\ep05", pass: "final", zoom: 60 });
  });

  it("ignores junk and missing storage", () => {
    expect(loadPrefs(memStorage({ [PREFS_KEY]: "{not json" }))).toEqual({});
    expect(loadPrefs(memStorage({ [PREFS_KEY]: `{"ep":5,"pass":"weird","zoom":"x"}` }))).toEqual({ ep: null, pass: undefined, zoom: undefined });
    expect(loadPrefs(null)).toEqual({});
    const throwing = { getItem: () => { throw new Error("denied"); }, setItem: () => { throw new Error("denied"); } };
    expect(loadPrefs(throwing)).toEqual({});
    expect(() => savePrefs({ ep: null, pass: "proxy", zoom: 1 }, throwing)).not.toThrow();
  });

  it("initialState restores them, clamping zoom", () => {
    const s = initialState({ ep: "E", pass: "final", zoom: 9999 });
    expect(s.ep).toBe("E");
    expect(s.pass).toBe("final");
    expect(s.zoom).toBe(ZOOM_MAX);
    expect(initialState().pass).toBe("proxy");
    expect(clampZoom(Number.NaN)).toBe(ZOOM_DEFAULT);
  });

  it("persistPrefs writes only when ep, pass or zoom change", () => {
    const st = createStore(initialState());
    const s = memStorage();
    persistPrefs(st, s);
    st.set({ shot: "sh020" });
    expect(s.setItem).not.toHaveBeenCalled();
    st.set({ ep: "E1" });
    st.set({ pass: "final" });
    expect(s.setItem).toHaveBeenCalledTimes(2);
    expect(loadPrefs(s)).toMatchObject({ ep: "E1", pass: "final" });
  });
});

describe("live render tracking", () => {
  const prompts = {
    p1: { ep: "E", pass: "proxy" as const, shot: "sh020", take: 3 },
    p2: { ep: "E", pass: "final" as const, shot: "sh020", take: 1 },
  };
  it("renderingTakes: the take whose prompt is running, for that shot and pass only", () => {
    expect([...renderingTakes({ running: "p1", prompts }, "E", "proxy", "sh020")]).toEqual([3]);
    expect(renderingTakes({ running: "p1", prompts }, "E", "final", "sh020").size).toBe(0);
    expect(renderingTakes({ running: "p1", prompts }, "E", "proxy", "sh030").size).toBe(0);
    expect(renderingTakes({ running: null, prompts }, "E", "proxy", "sh020").size).toBe(0);
    expect(renderingTakes({ running: "unknown", prompts }, "E", "proxy", "sh020").size).toBe(0);
  });
  it("promptOf finds a take's prompt id", () => {
    expect(promptOf({ prompts }, { ep: "E", pass: "final", shot: "sh020", take: 1 })).toBe("p2");
    expect(promptOf({ prompts }, { ep: "E", pass: "final", shot: "sh020", take: 2 })).toBeUndefined();
  });
});
