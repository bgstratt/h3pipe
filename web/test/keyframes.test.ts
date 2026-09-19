// Continuity keyframes: cut neighbours, the target note, the viewer's frame,
// the source caption, the HTTP route, and the mock's /refs/keyframe.
import { describe, expect, it } from "vitest";
import { createHttpApi } from "../src/api";
import { cutNeighbour, frameAt, keyframeNote, keyframeSource, liveTake, shotKeyframes, usesKeyframes } from "../src/lib/keyframes";
import { createMockApi } from "../src/mock/mockApi";
import { MOCK_TARGETS } from "../src/mock/mockTargets";
import type { EpisodeStatus, Ref, RefTake, TargetList } from "../src/types";

const st = (ids: string[], orphans: string[] = []) =>
  ({ shots: ids.map((shot) => ({ shot, orphan: orphans.includes(shot) })) }) as unknown as EpisodeStatus;

const TARGETS: TargetList = {
  targets: [
    { id: "minimax_h3_ref2va", kind: "video", label: "MiniMax H3 Ref2VA", capabilities: { keyframes: [] } },
    { id: "ltx2", kind: "video", label: "LTX-2.5 distilled (text / keyframes to video + audio)", short: "LTX-2", capabilities: { keyframes: ["first", "last"] } },
    { id: "krea2", kind: "image", label: "Krea 2" },
  ],
  default: { video: "minimax_h3_ref2va" },
};

describe("keyframes", () => {
  it("finds the neighbours in cut order, skipping orphans", () => {
    const s = st(["sh010", "sh030", "sh020", "sh040"], ["sh020"]);
    expect(cutNeighbour(s, "sh030", -1)).toBe("sh010");
    expect(cutNeighbour(s, "sh030", 1)).toBe("sh040"); // sh020 is an orphan
    expect(cutNeighbour(s, "sh010", -1)).toBeNull();
    expect(cutNeighbour(s, "sh040", 1)).toBeNull();
    expect(cutNeighbour(s, "sh999", -1)).toBeNull();
    expect(cutNeighbour(undefined, "sh010", 1)).toBeNull();
  });

  it("says which targets read keyframes, from the capabilities", () => {
    expect(usesKeyframes(TARGETS, "ltx2")).toBe(true);
    expect(usesKeyframes(TARGETS, "minimax_h3_ref2va")).toBe(false);
    expect(usesKeyframes(TARGETS, "nope")).toBeNull();
    expect(usesKeyframes({ targets: [{ id: "x", kind: "video", label: "X" }], default: {} }, "x")).toBeNull();
    expect(keyframeNote(TARGETS, "minimax_h3_ref2va")).toBe("used by LTX; not by MiniMax H3 Ref2VA");
    expect(keyframeNote(TARGETS, "ltx2")).toBeNull();
    expect(keyframeNote(null, "minimax_h3_ref2va")).toBeNull(); // the server doesn't say: no note
    expect(keyframeNote(MOCK_TARGETS, "minimax_h3_ref2va")).toBe("used by LTX, Wan; not by MiniMax H3 Ref2VA");
  });

  it("turns the viewer's playhead into a frame, the end into 'last'", () => {
    expect(frameAt(0, 24, 3.0417)).toBe(0);
    expect(frameAt(1 / 24, 24, 3.0417)).toBe(1);
    expect(frameAt(0.99999 / 24 * 10, 24, 3.0417)).toBe(10); // a frame-stepped time a hair short
    expect(frameAt(0.5 / 24, 24, 3.0417)).toBe(0);           // mid-frame: the one on screen
    expect(frameAt(72 / 24, 24, 73 / 24)).toBe(72);          // stepped onto the last frame
    expect(frameAt(73 / 24, 24, 73 / 24)).toBe("last");      // the end of the clip
    expect(frameAt(1, 24, 3, true)).toBe("last");            // ended
    expect(frameAt(2, 0, NaN)).toBe(48);                     // unknown fps: 24
  });

  it("captions a frame take and finds a shot's keyframes", () => {
    const t = { take: 2, source: "frame", from: { shot: "sh010", take: 3, pass: "proxy", frame: 72, frames: 73 } } as RefTake;
    expect(keyframeSource(t)).toBe("sh010 t03 · frame 72 of 73");
    expect(keyframeSource({ ...t, from: { ...t.from!, pass: "final" } })).toBe("sh010 final t03 · frame 72 of 73");
    expect(keyframeSource({ ...t, source: "imported" })).toBeNull();
    const r = { id: "shot:sh020:first", picked: 2, takes: [{ ...t, take: 1 }, t] } as unknown as Ref;
    expect(liveTake(r)).toBe(t);
    expect(shotKeyframes([r], "sh020")).toEqual({ first: r });
    expect(shotKeyframes(undefined, "sh020")).toEqual({});
  });

  it("posts /h3pipe/refs/keyframe with the body as given", async () => {
    const calls: { path: string; body: string }[] = [];
    const api = createHttpApi({
      fetch: async (path, init) => {
        calls.push({ path, body: String(init?.body) });
        return new Response(`{"id":"shot:sh020:first","takes":[],"picked":null}`, { status: 200 });
      },
      url: (p) => p,
    });
    const r = await api.refsKeyframe({ ep: "C:\\ep05", pass: "proxy", shot: "sh020", which: "first", source_shot: "sh010", source_take: 2, frame: 40 });
    expect(r.id).toBe("shot:sh020:first");
    expect(calls[0].path).toBe("/h3pipe/refs/keyframe");
    expect(JSON.parse(calls[0].body)).toEqual({ ep: "C:\\ep05", pass: "proxy", shot: "sh020", which: "first", source_shot: "sh010", source_take: 2, frame: 40 });
  });
});

describe("mock /refs/keyframe", () => {
  it("cuts the previous shot's last frame, picks it, then only adds", async () => {
    const events: [string, unknown][] = [];
    const api = createMockApi((e, d) => events.push([e, d]), { latency: 0 });
    const ep = (await api.episodes())[0].ep;
    const cut = (await api.episode(ep, "proxy")).shots.filter((s) => !s.orphan);
    // the first shot whose previous shot has a usable take
    const i = cut.findIndex((_, k) => k > 0 && cut[k - 1].cut.usable);
    expect(i).toBeGreaterThan(0);
    const shot = cut[i].shot;
    const prev = cut[i - 1];
    let r = await api.refsKeyframe({ ep, pass: "proxy", shot, which: "first" });
    expect(r).toMatchObject({ id: `shot:${shot}:first`, kind: "keyframe", scope: "shot", exists: true, picked: 1 });
    expect(r.takes[0]).toMatchObject({ source: "frame", from: { shot: prev.shot, take: prev.cut.take, pass: "proxy", frame: (prev.length ?? 73) - 1 } });
    expect(events.filter(([e]) => e === "h3pipe.ref").map(([, d]) => (d as { status: string }).status)).toEqual(["ok", "picked"]);
    expect((await api.refs(ep)).refs.map((x) => x.id)).toContain(`shot:${shot}:first`);
    r = await api.refsKeyframe({ ep, pass: "proxy", shot, which: "first", frame: 0 });
    expect(r.picked).toBe(1); // a live keyframe: the new take waits for a pick
    expect(r.takes[1].from!.frame).toBe(0);
    await expect(api.refsKeyframe({ ep, pass: "proxy", shot: cut[0].shot, which: "first" })).rejects.toMatchObject({ status: 400 });
    await expect(api.refsKeyframe({ ep, pass: "proxy", shot, which: "first", frame: 9999 })).rejects.toMatchObject({ status: 400 });
    await expect(api.refsKeyframe({ ep, pass: "proxy", shot, which: "first", source_shot: prev.shot, source_take: 99 })).rejects.toMatchObject({ status: 404 });
  });
});
