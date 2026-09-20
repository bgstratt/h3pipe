// Phase 9d: a shot's audio from elsewhere. The offset / start / gain maths and
// its clamping, the badge's words, then the edits end to end on the mock API
// (optimistic, undoable, saved as one PUT /h3pipe/cut), and SSR of the new
// window and the surfaces that show the badge.
import { createElement } from "react";
import { renderToString } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  browseClipAudio, closeClipAudio, openClipAudio, refreshEpisode, setClipAudioDraft, uploadClipAudio,
} from "../src/actions";
import { ApiError } from "../src/api";
import {
  clearClipAudio, clipAudioOf, clipAudioUnchanged, copyCut, cutSettled, peaksCache, resetCut, resetCutHistory,
  setClipAudio, setCutAudio, toggleLock, undoCut, redoCut, undoStack,
} from "../src/cutActions";
import { setApi, setHost, type Host } from "../src/host";
import { AudioBadge, ClipAudioWindow, draftFile } from "../src/components/ClipAudio";
import { ContextMenu } from "../src/components/ContextMenu";
import { CutMenu } from "../src/components/CutMenu";
import { CutSection } from "../src/components/CutSection";
import { Timeline } from "../src/components/Timeline";
import {
  GAIN_DEFAULT, GAIN_MAX, audioBadgeOf, audioLayout, audioNote, audioOf, audioProblem, audioShot, audioWhy, baseName,
  clampGain, clampOffset, clampStart, clipBox, clipsWithAudio, entryAudio, gainOf, normalizeAudio, offsetOf, pictureBox,
  previewAt, resolveAudioFile, sameAudio, soundShots, startOf, takeAudioFile, takesWithSound,
} from "../src/lib/audioSource";
import { epRelative } from "../src/lib/browse";
import { clearAudio, entriesOf, entryOf, fieldsOf, sameEntries, withFields, applyEntries } from "../src/lib/cutEdit";
import { diffEdit, applyEdit } from "../src/lib/undo";
import { checkAudio, checkEntries, CutError, materialize, resetEntries } from "../src/mock/mockCut";
import { createMockApi } from "../src/mock/mockApi";
import { initialState, statusKey, store } from "../src/store";
import type { CutAudioSource, CutEntry, CutInfo, EpisodeStatus, ShotStatus, TakeSummary } from "../src/types";

// ---------------------------------------------------------------------------
// fixtures
// ---------------------------------------------------------------------------

function take(n: number, over: Partial<TakeSummary> = {}): TakeSummary {
  return {
    take: n, status: "ok", has_video: true, seed: "1", seed_source: "stable", note: "", overrides: [], stale: [],
    thumb: null, strip: null, mp4: `r/t${n}.mp4`, queued: null, finished: null, save_notes: "", ...over,
  };
}

const CUT: CutInfo = {
  take: 1, picked: false, pass: "proxy", placeholder: false, usable: true, trim_in: 0, trim_out: 0, locked: false,
  note: "", in_cut_file: false,
};

function shot(id: string, length = 48, cut: Partial<CutInfo> = {}, takes?: TakeSummary[]): ShotStatus {
  return {
    shot: id, orphan: false, sequence: "sq01", length, seconds: length / 24, size: null, subjects: [], audio_policy: null,
    cut: { ...CUT, ...cut }, override: { fields: [], stale: false },
    takes: takes ?? [take(1, { mp4: `r/${id}/t1.mp4`, frames: length, audio: `r/${id}/t1.mp4` })],
  };
}

function ep(shots: ShotStatus[], over: Partial<EpisodeStatus> = {}): EpisodeStatus {
  return { episode: "ep", title: "", pass: "proxy", fps: 24, width: 448, height: 256, folder: "renders_proxy", shots, ...over };
}

const TAKE: CutAudioSource = { source: "take", shot: "sh020", take: 1, pass: "proxy" };

// ---------------------------------------------------------------------------
// the maths
// ---------------------------------------------------------------------------

describe("the clock: where the source is heard under the picture", () => {
  it("with no offset the source runs from `start` for the clip's whole length", () => {
    const L = audioLayout({ source: "file", path: "a.wav", start: 2 }, 4, 20);
    expect(L).toMatchObject({ head: 0, used: 4, tail: 0, srcIn: 2, srcOut: 6 });
    expect(L.short).toBe(false);
    expect(L.silent).toBe(false);
  });
  it("a positive offset puts silence first and keeps the clip's length", () => {
    const L = audioLayout({ source: "file", path: "a.wav", start: 2, offset: 1.5 }, 4, 20);
    expect(L).toMatchObject({ head: 1.5, used: 2.5, tail: 0, srcIn: 2, srcOut: 4.5 });
    expect(L.head + L.used + L.tail).toBe(4);
    expect(L.short).toBe(true);
  });
  it("a negative offset starts further into the source, never before it", () => {
    const L = audioLayout({ source: "file", path: "a.wav", start: 2, offset: -1 }, 4, 20);
    expect(L).toMatchObject({ head: 0, used: 4, srcIn: 3, srcOut: 7 });
  });
  it("a source that runs out leaves silence at the end", () => {
    const L = audioLayout({ source: "file", path: "a.wav", start: 8 }, 4, 10);
    expect(L).toMatchObject({ head: 0, used: 2, tail: 2, srcIn: 8, srcOut: 10 });
    expect(L.short).toBe(true);
  });
  it("a start past the end of the source is heard as nothing at all", () => {
    const L = audioLayout({ source: "file", path: "a.wav", start: 12 }, 4, 10);
    expect(L).toMatchObject({ used: 0, tail: 4 });
    expect(L.silent).toBe(true);
    expect(audioNote(L, 4)).toContain("silent");
  });
  it("an offset past the clip's length is all silence too", () => {
    const L = audioLayout({ source: "file", path: "a.wav", offset: 4 }, 4, 10);
    expect(L).toMatchObject({ head: 4, used: 0, tail: 0 });
    expect(L.silent).toBe(true);
  });
  it("the picture window in source time is `start - offset` for the clip's length", () => {
    const L = audioLayout({ source: "file", path: "a.wav", start: 3, offset: 1 }, 5, 30);
    expect(L.pictureIn).toBe(2);
    expect(L.pictureOut).toBe(7);
    // the audible part never begins before `start`
    expect(L.srcIn).toBe(3);
  });
  it("without the source's length it assumes it is long enough (the peaks redraw it)", () => {
    const L = audioLayout({ source: "take", shot: "sh020", take: 1 }, 6, null);
    expect(L).toMatchObject({ head: 0, used: 6, tail: 0, short: false });
  });
  it("the layout adds up to the clip's length for any start / offset", () => {
    for (const start of [0, 1.25, 7, 30]) {
      for (const offset of [-3, -0.5, 0, 0.5, 3]) {
        const L = audioLayout({ source: "file", path: "a.wav", start, offset }, 4, 10);
        expect(Math.round((L.head + L.used + L.tail) * 1000) / 1000).toBe(4);
        expect(L.used).toBeGreaterThanOrEqual(0);
        expect(L.srcIn).toBeGreaterThanOrEqual(start);
      }
    }
  });
});

describe("previewAt: the source time the player seeks to", () => {
  const a: CutAudioSource = { source: "file", path: "a.wav", start: 2, offset: 1 };
  it("is silent before the offset", () => {
    expect(previewAt(a, 0, 4, 20)).toBeNull();
    expect(previewAt(a, 0.9, 4, 20)).toBeNull();
  });
  it("is `start` at the offset, and runs a second a second after it", () => {
    expect(previewAt(a, 1, 4, 20)).toBe(2);
    expect(previewAt(a, 2.5, 4, 20)).toBe(3.5);
  });
  it("is silent past the end of the source", () => {
    expect(previewAt({ source: "file", path: "a.wav", start: 9 }, 3, 4, 10)).toBeNull();
    expect(previewAt(null, 1, 4, 10)).toBeNull();
  });
});

describe("clamping (what the window's handles and fields allow)", () => {
  it("start is 0 or more and inside the source", () => {
    expect(clampStart(-4, 10)).toBe(0);
    expect(clampStart(3.4567, 10)).toBe(3.457);
    expect(clampStart(99, 10)).toBe(10);
    expect(clampStart(99, null)).toBe(99);
    expect(clampStart(Number.NaN, 10)).toBe(0);
  });
  it("offset never shifts further than the clip's own length either way", () => {
    expect(clampOffset(9, 4)).toBe(4);
    expect(clampOffset(-9, 4)).toBe(-4);
    expect(clampOffset(1.2345, 4)).toBe(1.235);
    expect(clampOffset(2, 0)).toBe(2); // the length isn't known yet
  });
  it("gain stays inside 0–4, the range the server takes", () => {
    expect(clampGain(-1)).toBe(0);
    expect(clampGain(9)).toBe(GAIN_MAX);
    expect(clampGain(1.234)).toBe(1.23);
    expect(clampGain(Number.NaN)).toBe(GAIN_DEFAULT);
  });
  it("the defaults read back as 0 / 0 / 1 whatever is missing", () => {
    expect(startOf(null)).toBe(0);
    expect(offsetOf(undefined)).toBe(0);
    expect(gainOf({ source: "none" })).toBe(GAIN_DEFAULT);
  });
});

describe("the boxes the window draws", () => {
  it("the picture marker sits at start - offset, the clip's length wide", () => {
    // 20 s of source in 200 px: 10 px a second
    expect(pictureBox({ source: "file", path: "a.wav", start: 4, offset: 1 }, 5, 20, 200)).toEqual({ left: 30, width: 50 });
  });
  it("the audio's placement over the clip is the head silence, then what is used", () => {
    // a 4 s clip in 100 px: 25 px a second; 1 s of silence then 2.5 s of sound
    expect(clipBox({ source: "file", path: "a.wav", start: 2, offset: 1 }, 4, 20, 100)).toEqual({ left: 25, width: 75 });
  });
  it("a silent placement is never drawn narrower than a pixel", () => {
    expect(clipBox({ source: "file", path: "a.wav", start: 99 }, 4, 10, 100).width).toBe(1);
  });
});

// ---------------------------------------------------------------------------
// writing the entry
// ---------------------------------------------------------------------------

describe("normalizeAudio writes what cut.json holds", () => {
  it("leaves the defaults out", () => {
    expect(normalizeAudio({ source: "take", shot: "sh020", take: 1, pass: "proxy", start: 0, offset: 0, gain: 1 }))
      .toEqual({ source: "take", shot: "sh020", take: 1, pass: "proxy" });
  });
  it("keeps the numbers that aren't defaults, rounded to the millisecond", () => {
    expect(normalizeAudio({ source: "file", path: "audio/x.wav", start: 1.23456, offset: -0.5, gain: 2 }))
      .toEqual({ source: "file", path: "audio/x.wav", start: 1.235, offset: -0.5, gain: 2 });
  });
  it("silence carries nothing else", () => {
    expect(normalizeAudio({ source: "none", start: 3, gain: 2 })).toEqual({ source: "none" });
  });
  it("fills the clip's own shot for a take source that doesn't name one", () => {
    expect(normalizeAudio({ source: "take", take: 2 }, "sh040")).toEqual({ source: "take", shot: "sh040", take: 2 });
    expect(audioShot({ source: "take", take: 2 }, "sh040")).toBe("sh040");
  });
  it("a file source with no path, and an unknown source, are 'its own sound'", () => {
    expect(normalizeAudio({ source: "file" })).toBeNull();
    expect(normalizeAudio({ source: "wat" } as unknown as CutAudioSource)).toBeNull();
    expect(normalizeAudio(null)).toBeNull();
  });
  it("backslashes in a path become forward slashes, as the routes want", () => {
    expect(normalizeAudio({ source: "file", path: "audio\\x.wav" })?.path).toBe("audio/x.wav");
  });
  it("sameAudio ignores the fields that write the same file", () => {
    expect(sameAudio({ source: "take", shot: "a", take: 1, gain: 1 }, { source: "take", shot: "a", take: 1 })).toBe(true);
    expect(sameAudio({ source: "take", shot: "a", take: 1 }, { source: "take", shot: "a", take: 2 })).toBe(false);
    expect(sameAudio(null, { source: "file" })).toBe(true); // both mean "its own"
    expect(sameAudio(null, { source: "none" })).toBe(false);
  });
});

describe("audioProblem: what PUT /h3pipe/cut would refuse", () => {
  it("takes the clip's own sound and silence without complaint", () => {
    expect(audioProblem(null)).toBeNull();
    expect(audioProblem({ source: "none" })).toBeNull();
  });
  it("wants a shot and a take with sound", () => {
    expect(audioProblem({ source: "take" })).toContain("Pick the shot");
    expect(audioProblem({ source: "take", shot: "sh020" })).toContain("Pick a take of sh020");
    expect(audioProblem({ source: "take", shot: "sh020", take: 3 }, false)).toContain("sh020 t03 has no sound");
    expect(audioProblem({ source: "take", shot: "sh020", take: 3 })).toBeNull();
  });
  it("wants a path inside the episode, with an audio stream", () => {
    expect(audioProblem({ source: "file" })).toContain("Pick a file");
    expect(audioProblem({ source: "file", path: "C:/elsewhere/x.wav" })).toContain("outside the episode");
    expect(audioProblem({ source: "file", path: "audio/x.wav" }, false)).toContain("x.wav has no audio stream");
    expect(audioProblem({ source: "file", path: "audio/x.wav" })).toBeNull();
  });
  it("refuses a negative start and a gain outside 0–4", () => {
    expect(audioProblem({ source: "file", path: "a.wav", start: -1 })).toContain("0 s or more");
    expect(audioProblem({ source: "file", path: "a.wav", gain: 9 })).toContain("between 0 and 4");
    expect(audioProblem({ source: "file", path: "a.wav", gain: -0.5 })).toContain("between 0 and 4");
  });
  it("names an unknown source", () => {
    expect(audioProblem({ source: "elsewhere" } as unknown as CutAudioSource)).toContain("Unknown audio source");
  });
});

// ---------------------------------------------------------------------------
// the badge
// ---------------------------------------------------------------------------

describe("the speaker badge's words", () => {
  it("a take reads as the shot and take, a file as its name, silence as silent", () => {
    expect(audioWhy({ source: "take", shot: "sh020", take: 1 })).toBe("sh020 t01");
    expect(audioWhy({ source: "take", take: 3 }, "sh040")).toBe("sh040 t03");
    expect(audioWhy({ source: "file", path: "audio/line_sh030.wav" })).toBe("line_sh030.wav");
    expect(audioWhy({ source: "none" })).toBe("silent");
    expect(audioWhy(null)).toBe("");
  });
  it("the server's `audio_why` wins over the one worked out here", () => {
    const cut = { ...CUT, audio: TAKE, audio_why: "sh020 t01 (final)" };
    expect(audioBadgeOf(cut, "sh010")!.label).toBe("sh020 t01 (final)");
  });
  it("there is no badge for a clip playing its own sound", () => {
    expect(audioBadgeOf(CUT, "sh010")).toBeNull();
  });
  it("the tooltip says what plays, how it sits and where the file is", () => {
    const cut = { ...CUT, audio: { ...TAKE, start: 0.5, offset: -0.2, gain: 1.5 }, audio_file: "renders_proxy/sh020/sh020_t01.mp4" };
    const b = audioBadgeOf(cut, "sh010")!;
    expect(b.kind).toBe("take");
    expect(b.title).toContain("sh010 plays sh020 t01's sound (proxy) instead of its own");
    expect(b.title).toContain("from 0.50 s in");
    expect(b.title).toContain("-0.20 s against the picture");
    expect(b.title).toContain("gain 1.5");
    expect(b.title).toContain("renders_proxy/sh020/sh020_t01.mp4");
    expect(b.title).toContain("Audio from…");
  });
  it("silence reads as muted", () => {
    const b = audioBadgeOf({ ...CUT, audio: { source: "none" } }, "sh040")!;
    expect(b.kind).toBe("none");
    expect(b.label).toBe("silent");
    expect(b.title).toContain("plays silent");
  });
  it("baseName handles both separators and an empty path", () => {
    expect(baseName("a/b/c.wav")).toBe("c.wav");
    expect(baseName("a\\b\\c.wav")).toBe("c.wav");
    expect(baseName(null)).toBe("");
  });
  it("clipsWithAudio names the clips that don't play their own", () => {
    const st = ep([shot("sh010"), shot("sh020", 48, { audio: TAKE }), shot("sh030", 48, { audio: { source: "none" } })]);
    expect(clipsWithAudio(st.shots)).toEqual(["sh020", "sh030"]);
  });
});

// ---------------------------------------------------------------------------
// resolving the source to a file
// ---------------------------------------------------------------------------

describe("what a source will actually play", () => {
  const shots = [shot("sh010"), shot("sh020")];
  it("a take source is that take's audio file", () => {
    expect(takeAudioFile(shots, "sh020", 1)).toBe("r/sh020/t1.mp4");
    expect(takeAudioFile(shots, "sh020", 9)).toBeNull();
    expect(resolveAudioFile(TAKE, "sh010", "proxy", shots)).toBe("r/sh020/t1.mp4");
  });
  it("a file source is its own path, silence is nothing", () => {
    expect(resolveAudioFile({ source: "file", path: "audio/x.wav" }, "sh010", "proxy", shots)).toBe("audio/x.wav");
    expect(resolveAudioFile({ source: "none" }, "sh010", "proxy", shots)).toBeNull();
    expect(resolveAudioFile(null, "sh010", "proxy", shots)).toBeNull();
  });
  it("a take in the other pass isn't resolved here (the server sends audio_file)", () => {
    expect(resolveAudioFile({ ...TAKE, pass: "final" }, "sh010", "proxy", shots)).toBeNull();
  });
  it("takesWithSound and soundShots only offer what can be heard, this clip first", () => {
    const list = [
      shot("sh010", 48, {}, [take(1, { audio: null }), take(2, { audio: "r/sh010/t2.mp4" })]),
      shot("sh020", 48, {}, [take(1, { status: "failed", audio: "x.mp4" })]),
      shot("sh030"),
    ];
    expect(takesWithSound(list[0].takes).map((t) => t.take)).toEqual([2]);
    expect(takesWithSound(list[1].takes)).toEqual([]);
    expect(soundShots(list, "sh030").map((s) => s.shot)).toEqual(["sh030", "sh010"]);
  });
  it("draftFile reads a take from whichever pass names it", () => {
    const st = ep(shots);
    const other = ep([shot("sh020", 48, {}, [take(4, { mp4: "rf/sh020/t4.mp4", audio: "rf/sh020/t4.mp4" })])], { pass: "final" });
    expect(draftFile(TAKE, "sh010", "proxy", st, other)).toBe("r/sh020/t1.mp4");
    expect(draftFile({ ...TAKE, take: 4, pass: "final" }, "sh010", "proxy", st, other)).toBe("rf/sh020/t4.mp4");
    expect(draftFile({ source: "none" }, "sh010", "proxy", st, other)).toBeNull();
  });
});

describe("epRelative: a browsed path as cut.json wants it", () => {
  const EP = "C:\\Shows\\DeanStories\\ep05";
  it("takes a file inside the episode", () => {
    expect(epRelative("C:\\Shows\\DeanStories\\ep05\\audio\\x.wav", EP)).toBe("audio/x.wav");
    // case and separators don't matter on Windows
    expect(epRelative("c:/shows/deanstories/ep05/audio/x.wav", EP)).toBe("audio/x.wav");
  });
  it("takes one beside a parent-folder series config, as `../`", () => {
    expect(epRelative("C:\\Shows\\DeanStories\\audio\\x.wav", EP)).toBe("../audio/x.wav");
  });
  it("refuses anything else", () => {
    expect(epRelative("D:\\Stock\\voices\\x.wav", EP)).toBeNull();
    expect(epRelative("C:\\Shows\\x.wav", EP)).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// the entries: cut.json, the fields, undo
// ---------------------------------------------------------------------------

describe("audio travels with the cut entries", () => {
  it("entryOf writes it as the file holds it, the clip's own shot filled in", () => {
    const s = shot("sh010", 48, { audio: { source: "take", take: 2, start: 0, gain: 1 } });
    expect(entryOf(s, "proxy")).toEqual({ shot: "sh010", audio: { source: "take", shot: "sh010", take: 2 } });
    expect(entryOf(shot("sh010"), "proxy")).toEqual({ shot: "sh010" });
  });
  it("fieldsOf / withFields carry it beside the trims and the lock", () => {
    const list: CutEntry[] = [{ shot: "a" }, { shot: "b", trim_in: 3 }];
    expect(fieldsOf(list[1])).toEqual({ trim_in: 3, trim_out: 0, locked: false, audio: null });
    const out = withFields(list, "b", { audio: TAKE });
    expect(out[1]).toEqual({ shot: "b", trim_in: 3, audio: TAKE });
    // and the trims survive a later audio change, and the other way round
    expect(withFields(out, "b", { audio: null })[1]).toEqual({ shot: "b", trim_in: 3 });
    expect(withFields(out, "b", { trim_in: 0 })[1]).toEqual({ shot: "b", audio: TAKE });
  });
  it("sameEntries notices an audio change, and ignores a rewritten default", () => {
    const a: CutEntry[] = [{ shot: "x", audio: TAKE }];
    expect(sameEntries(a, [{ shot: "x", audio: { ...TAKE, gain: 1 } }])).toBe(true);
    expect(sameEntries(a, [{ shot: "x", audio: { ...TAKE, gain: 2 } }])).toBe(false);
    expect(sameEntries(a, [{ shot: "x" }])).toBe(false);
  });
  it("clearAudio puts every clip back except a locked one", () => {
    const list: CutEntry[] = [{ shot: "a", audio: TAKE }, { shot: "b", audio: { source: "none" }, locked: true }, { shot: "c" }];
    expect(clearAudio(list)).toEqual([{ shot: "a" }, { shot: "b", audio: { source: "none" }, locked: true }, { shot: "c" }]);
  });
  it("entryAudio reads an entry's source back", () => {
    expect(entryAudio({ shot: "a", audio: { source: "none" } })).toEqual({ source: "none" });
    expect(entryAudio({ shot: "a" })).toBeNull();
    expect(audioOf({ audio: { source: "take" } })).toMatchObject({ source: "take" });
    expect(audioOf({ audio: { nope: 1 } as unknown as CutAudioSource })).toBeNull();
  });
  it("applyEntries shows the new source at once, with what will play and the badge's words", () => {
    const st = ep([shot("sh010"), shot("sh020")]);
    const next = applyEntries(st, [{ shot: "sh010", audio: TAKE }, { shot: "sh020" }]);
    expect(next.shots[0].cut.audio).toEqual(TAKE);
    expect(next.shots[0].cut.audio_file).toBe("r/sh020/t1.mp4");
    expect(next.shots[0].cut.audio_why).toBe("sh020 t01");
    // clearing it wipes the three of them
    const back = applyEntries(next, [{ shot: "sh010" }, { shot: "sh020" }]);
    expect(back.shots[0].cut.audio).toBeNull();
    expect(back.shots[0].cut.audio_why).toBeNull();
  });
  it("undo remembers an audio change and puts it back", () => {
    const before: CutEntry[] = [{ shot: "a" }, { shot: "b" }];
    const after = withFields(before, "b", { audio: TAKE });
    const edit = diffEdit("Audio of b", before, after)!;
    expect(edit.order).toBeNull();
    expect(edit.fields.b.after.audio).toEqual(TAKE);
    expect(applyEdit(after, edit, "undo")).toEqual(before);
    expect(applyEdit(before, edit, "redo")).toEqual(after);
  });
  it("undoing an audio change doesn't undo a trim made since", () => {
    const before: CutEntry[] = [{ shot: "a" }, { shot: "b" }];
    const edit = diffEdit("Audio of a", before, withFields(before, "a", { audio: TAKE }))!;
    const since: CutEntry[] = [{ shot: "a", audio: TAKE }, { shot: "b", trim_in: 5 }];
    expect(applyEdit(since, edit, "undo")).toEqual([{ shot: "a" }, { shot: "b", trim_in: 5 }]);
  });
});

// ---------------------------------------------------------------------------
// the mock's own checks (what the route would answer)
// ---------------------------------------------------------------------------

describe("the mock's PUT /h3pipe/cut audio checks", () => {
  const ok = () => "a.wav";
  const put = (audio: unknown) => () => checkEntries([{ shot: "sh010", audio }], 24, () => null, ok);
  it("takes a well-formed source", () => {
    expect(put({ source: "take", shot: "sh020", take: 1 })()).toHaveLength(1);
    expect(put({ source: "file", path: "audio/x.wav", start: 1, offset: -2, gain: 0.5 })()).toHaveLength(1);
    expect(put({ source: "none" })()).toHaveLength(1);
    expect(put(null)()).toHaveLength(1);
  });
  it("400s an unknown key, source, pass or shape", () => {
    expect(put({ source: "take", take: 1, nope: 1 })).toThrow(/unknown field nope/);
    expect(put({ source: "elsewhere" })).toThrow(/must be take, file or none/);
    expect(put({ source: "take", take: 1, pass: "draft" })).toThrow(/unknown pass/);
    expect(put(7)).toThrow(/must be an object or null/);
  });
  it("400s a negative start, a bad offset and a gain outside 0–4", () => {
    expect(put({ source: "file", path: "a.wav", start: -1 })).toThrow(/0 s or more/);
    expect(put({ source: "file", path: "a.wav", offset: "soon" })).toThrow(/number of seconds/);
    expect(put({ source: "file", path: "a.wav", gain: 5 })).toThrow(/between 0 and 4/);
  });
  it("400s a path outside the episode, and a take with no sound", () => {
    expect(put({ source: "file", path: "C:/x.wav" })).toThrow(/outside the episode/);
    expect(put({ source: "file", path: "../../x.wav" })).toThrow(/outside the episode/);
    expect(() => checkAudio({ shot: "sh010", audio: { source: "take", shot: "sh020", take: 4 } }, () => null))
      .toThrow(/sh020 t04 doesn't exist or has no sound/);
    expect(() => checkAudio({ shot: "sh010", audio: { source: "file", path: "audio/x.wav" } }, () => null))
      .toThrow(/audio\/x.wav has no audio stream/);
  });
  it("the errors are 400s", () => {
    try {
      put({ source: "elsewhere" })();
      expect.unreachable();
    } catch (e) {
      expect(e).toBeInstanceOf(CutError);
      expect((e as CutError).status).toBe(400);
    }
  });
  it("reset 'audio' clears every source but a locked clip's; 'trims' leaves them alone", () => {
    const list: CutEntry[] = [{ shot: "a", audio: TAKE, trim_in: 2 }, { shot: "b", audio: { source: "none" }, locked: true }];
    expect(resetEntries(list, "proxy", ["a", "b"], "audio")).toEqual([
      { shot: "a", trim_in: 2 }, { shot: "b", locked: true, audio: { source: "none" } },
    ]);
    expect(resetEntries(list, "proxy", ["a", "b"], "trims")[0]).toEqual({ shot: "a", audio: TAKE });
    expect(resetEntries(list, "proxy", ["a", "b"], "all")[0]).toEqual({ shot: "a" });
  });
  it("materialize keeps a source through a rewrite of the file", () => {
    expect(materialize([{ shot: "a", audio: TAKE }], "proxy", ["a", "b"])).toEqual([{ shot: "a", audio: TAKE }, { shot: "b" }]);
  });
});

// ---------------------------------------------------------------------------
// end to end on the mock API
// ---------------------------------------------------------------------------

describe("a clip's audio, end to end on the mock", () => {
  let api: ReturnType<typeof createMockApi>;
  let EP: string;
  const toasts: { sev: string; summary: string; detail?: string }[] = [];
  const st = () => store.get().status[statusKey(EP, "proxy")];
  const cutOf = (shotId: string) => st().shots.find((s) => s.shot === shotId)!.cut;

  beforeEach(async () => {
    toasts.length = 0;
    peaksCache.forget();
    resetCutHistory();
    setHost({ on: () => () => {}, toast: (sev, summary, detail) => void toasts.push({ sev, summary, detail }), show: () => {} } as Host);
    api = createMockApi(() => {}, { latency: 0 });
    setApi(api);
    EP = (await api.episodes())[0].ep;
    store.set(initialState());
    store.set({ ep: EP, pass: "proxy" });
    await refreshEpisode(EP, "proxy");
  });
  afterEach(() => store.set(initialState()));

  it("borrows another take's sound, and the status carries audio / audio_file / audio_why", async () => {
    expect(await setClipAudio("sh010", { source: "take", shot: "sh020", take: 1, pass: "proxy" })).toBe(true);
    // shown before the server answers
    expect(cutOf("sh010").audio).toMatchObject({ source: "take", shot: "sh020", take: 1 });
    await cutSettled();
    await refreshEpisode(EP, "proxy");
    const cut = cutOf("sh010");
    expect(cut.audio).toMatchObject({ source: "take", shot: "sh020", take: 1 });
    expect(cut.audio_file).toMatch(/sh020/);
    expect(cut.audio_why).toBe("sh020 t01");
    // and it is one entry of the whole list, nothing else moved
    const file = (await api.putCut(EP, "proxy", entriesOf(st()))).cut;
    expect(file.proxy!.find((e) => e.shot === "sh010")!.audio).toMatchObject({ source: "take", shot: "sh020" });
  });

  it("start, offset and gain are stored and come back", async () => {
    await setClipAudio("sh010", { source: "take", shot: "sh020", take: 1, pass: "proxy", start: 0.4, offset: -0.25, gain: 1.6 });
    await cutSettled();
    await refreshEpisode(EP, "proxy");
    expect(cutOf("sh010").audio).toMatchObject({ start: 0.4, offset: -0.25, gain: 1.6 });
  });

  it("a file inside the episode, and silence", async () => {
    expect(await setClipAudio("sh030", { source: "file", path: "audio/line_sh030.wav" })).toBe(true);
    await cutSettled();
    await refreshEpisode(EP, "proxy");
    expect(cutOf("sh030").audio_file).toBe("audio/line_sh030.wav");
    expect(cutOf("sh030").audio_why).toBe("line_sh030.wav");

    expect(await setClipAudio("sh040", { source: "none" })).toBe(true);
    await cutSettled();
    await refreshEpisode(EP, "proxy");
    expect(cutOf("sh040").audio).toEqual({ source: "none" });
    expect(cutOf("sh040").audio_file).toBeNull();
    expect(cutOf("sh040").audio_why).toBe("silent");
  });

  it("the server refuses a file with no audio stream, and the clip goes back", async () => {
    expect(await setClipAudio("sh010", { source: "file", path: "audio/no_sound.wav" })).toBe(false);
    await cutSettled();
    expect(cutOf("sh010").audio ?? null).toBeNull();
    expect(toasts.some((t) => t.sev === "error" && /Couldn't save the cut/.test(t.summary))).toBe(true);
  });

  it("undo and redo put the source back, and the history names it", async () => {
    await setClipAudio("sh010", { source: "take", shot: "sh020", take: 1, pass: "proxy" });
    await cutSettled();
    expect(undoStack(EP, "proxy").undoLabel).toBe("Audio of sh010 from sh020 t01");
    expect(await undoCut()).toBe(true);
    await cutSettled();
    expect(cutOf("sh010").audio ?? null).toBeNull();
    expect(await redoCut()).toBe(true);
    await cutSettled();
    expect(cutOf("sh010").audio).toMatchObject({ take: 1, shot: "sh020" });
  });

  it("clearing puts a clip back to its own sound", async () => {
    await setClipAudio("sh010", { source: "none" });
    await cutSettled();
    expect(clipAudioOf("sh010")).toEqual({ source: "none" });
    expect(clipAudioUnchanged("sh010", { source: "none" })).toBe(true);
    expect(await clearClipAudio("sh010")).toBe(true);
    await cutSettled();
    expect(clipAudioOf("sh010")).toBeNull();
  });

  it("a locked clip refuses a new source and says so", async () => {
    await toggleLock("sh010", true);
    await cutSettled();
    expect(await setClipAudio("sh010", { source: "none" })).toBe(false);
    expect(toasts.some((t) => t.sev === "warn" && /sh010 is locked/.test(t.summary))).toBe(true);
    expect(clipAudioOf("sh010")).toBeNull();
  });

  it("Cut ▸ Clear audio sources puts every clip back, and keeps a locked one", async () => {
    await setClipAudio("sh010", { source: "none" });
    await setClipAudio("sh030", { source: "file", path: "audio/line_sh030.wav" });
    await cutSettled();
    await toggleLock("sh030", true);
    await cutSettled();
    expect(await resetCut("audio")).toBe(true);
    await cutSettled();
    expect(clipAudioOf("sh010")).toBeNull();
    expect(clipAudioOf("sh030")).toMatchObject({ source: "file" });
    expect(toasts.some((t) => /Clear audio sources/.test(t.summary))).toBe(true);
  });

  it("Copy all from the other pass brings the audio sources over", async () => {
    store.set({ pass: "final" });
    await refreshEpisode(EP, "final");
    await setClipAudio("sh010", { source: "none" });
    await cutSettled();
    store.set({ pass: "proxy" });
    await refreshEpisode(EP, "proxy");
    expect(await copyCut("all")).toBe(true);
    await cutSettled();
    expect(clipAudioOf("sh010")).toEqual({ source: "none" });
  });

  it("the peaks route serves a loose episode file, so both waveforms can be drawn", async () => {
    const p = await api.peaks(EP, "audio/line_sh030.wav", 120);
    expect(p.peaks).toHaveLength(120);
    expect(p.duration).toBeGreaterThan(0);
    const silent = await api.peaks(EP, "audio/no_sound.wav", 120);
    expect(silent.silent).toBe(true);
  });

  it("uploading a file puts it in the episode and it becomes the draft", async () => {
    openClipAudio("sh030");
    setClipAudioDraft({ source: "file", path: "" });
    const f = new File([new Uint8Array(64)], "extra take.wav", { type: "audio/wav" });
    expect(await uploadClipAudio(f)).toBe(true);
    expect(store.get().clipAudio!.draft).toEqual({ source: "file", path: "audio/extra take.wav" });
    expect(store.get().clipAudio!.upload).toBeNull();
    expect(await setClipAudio("sh030", store.get().clipAudio!.draft)).toBe(true);
    await cutSettled();
    await refreshEpisode(EP, "proxy");
    expect(cutOf("sh030").audio_file).toBe("audio/extra take.wav");
  });

  it("a server without the upload route says to browse instead", async () => {
    openClipAudio("sh030");
    setApi({ ...api, uploadClipAudio: () => Promise.reject(new ApiError("nope", 404, "/h3pipe/audio/import")) });
    expect(await uploadClipAudio(new File([new Uint8Array(4)], "x.wav", { type: "audio/wav" }))).toBe(false);
    expect(store.get().clipAudio!.upload!.error).toContain("no upload route");
    setApi(api);
  });

  it("the file picker refuses something that isn't audio", async () => {
    openClipAudio("sh030");
    expect(await uploadClipAudio(new File([new Uint8Array(4)], "notes.txt", { type: "text/plain" }))).toBe(false);
    expect(store.get().clipAudio!.upload!.error).toBeTruthy();
  });

  it("the window opens on what the clip plays now, and Browse asks for audio", async () => {
    await setClipAudio("sh010", { source: "take", shot: "sh020", take: 1, pass: "proxy" });
    await cutSettled();
    openClipAudio("sh010");
    expect(store.get().clipAudio).toMatchObject({ shot: "sh010", pass: "proxy", draft: { source: "take", shot: "sh020" } });
    browseClipAudio("sh010");
    expect(store.get().browse).toEqual({ purpose: "clip-audio", files: "audio", shot: "sh010" });
    closeClipAudio();
    expect(store.get().clipAudio).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// the surfaces (rendered to HTML: a shape they can't take throws here)
// ---------------------------------------------------------------------------

describe("the Phase 9d surfaces render", () => {
  let api: ReturnType<typeof createMockApi>;
  let EP: string;

  beforeEach(async () => {
    setHost({ on: () => () => {}, toast: () => {}, show: () => {} } as Host);
    api = createMockApi(() => {}, { latency: 0 });
    setApi(api);
    EP = (await api.episodes())[0].ep;
    store.set(initialState());
    store.set({ ep: EP, pass: "proxy" });
    await refreshEpisode(EP, "proxy");
  });
  afterEach(() => store.set(initialState()));

  it("the window offers every source, the numbers and a preview", () => {
    openClipAudio("sh010");
    setClipAudioDraft({ source: "take", shot: "sh020", take: 1, pass: "proxy", start: 0.5, offset: 0.25, gain: 1.5 });
    const html = renderToString(createElement(ClipAudioWindow));
    // React 18 splits adjoining text nodes with comments, so the words are checked apart
    expect(html).toContain("Audio for");
    for (const label of ["its own", "another take", "a file", "silent", "Preview", "start", "offset", "gain", "Use this audio"]) {
      expect(html).toContain(label);
    }
    expect(html).toContain("sh020");
  });

  it("the file source shows the path, Browse and Upload", () => {
    openClipAudio("sh030");
    setClipAudioDraft({ source: "file", path: "audio/line_sh030.wav" });
    const html = renderToString(createElement(ClipAudioWindow));
    expect(html).toContain("audio/line_sh030.wav");
    expect(html).toContain("Browse…");
    expect(html).toContain("Upload…");
  });

  it("silence explains itself and draws no waveform", () => {
    openClipAudio("sh040");
    setClipAudioDraft({ source: "none" });
    const html = renderToString(createElement(ClipAudioWindow));
    expect(html).toContain("plays silent");
    expect(html).not.toContain("the clip's picture marked over it");
  });

  it("a locked clip can't be saved from the window", async () => {
    await toggleLock("sh010", true);
    await cutSettled();
    openClipAudio("sh010");
    setClipAudioDraft({ source: "none" });
    const html = renderToString(createElement(ClipAudioWindow));
    expect(html).toContain("is locked in the proxy cut");
    expect(html).toMatch(/data-clipaudio-save=""[^>]*disabled/);
  });

  it("with the recording on, the window says per-clip audio is ignored", () => {
    setCutAudio("recording");
    openClipAudio("sh010");
    setClipAudioDraft({ source: "none" });
    expect(renderToString(createElement(ClipAudioWindow))).toContain("Play all is on the recording");
    setCutAudio("clips");
  });

  it("the badge shows on a clip whose audio isn't its own, and dims in recording mode", () => {
    const cut = { ...CUT, audio: TAKE, audio_why: "sh020 t01" };
    expect(renderToString(createElement(AudioBadge, { shot: "sh010", cut }))).toContain("sh020 t01");
    const dim = renderToString(createElement(AudioBadge, { shot: "sh010", cut, dim: true }));
    expect(dim).toContain("h3-dim");
    expect(renderToString(createElement(AudioBadge, { shot: "sh010", cut: CUT }))).toBe("");
  });

  it("the timeline draws the badge on the clip", async () => {
    await setClipAudio("sh010", { source: "take", shot: "sh020", take: 1, pass: "proxy" });
    await cutSettled();
    store.set({ zoom: 60 });
    const html = renderToString(createElement(Timeline));
    expect(html).toContain("h3-b-audio");
    expect(html).toContain("sh020 t01");
  });

  it("the Inspector's Cut section shows it, with Clear", async () => {
    await setClipAudio("sh010", { source: "file", path: "audio/line_sh030.wav", offset: 0.5, gain: 2 });
    await cutSettled();
    await refreshEpisode(EP, "proxy");
    const html = renderToString(createElement(CutSection, { shot: "sh010" }));
    expect(html).toContain("line_sh030.wav");
    expect(html).toContain("+0.50 s against the picture");
    expect(html).toContain("gain 2");
    expect(html).toContain("Audio from…");
    expect(html).toContain("Clear");
    // a clip on its own sound says so and offers no Clear
    expect(renderToString(createElement(CutSection, { shot: "sh020" }))).toContain("its own take&#x27;s sound");
  });

  it("the clip's context menu has Audio from…", async () => {
    store.set({ menu: { x: 0, y: 0, shot: "sh010", pass: "proxy", take: 1 } });
    expect(renderToString(createElement(ContextMenu))).toContain("Audio from…");
    await setClipAudio("sh010", { source: "none" });
    await cutSettled();
    const html = renderToString(createElement(ContextMenu));
    expect(html).toContain("Back to its own audio");
    expect(html).toContain("silent");
  });

  it("the Cut menu offers Clear audio sources, counted", async () => {
    store.set({ cutMenu: { x: 0, y: 0 } });
    expect(renderToString(createElement(CutMenu))).toContain("Clear audio sources");
    await setClipAudio("sh010", { source: "none" });
    await cutSettled();
    expect(renderToString(createElement(CutMenu))).toContain("(1)");
    expect(renderToString(createElement(CutMenu))).toContain("Copy order, trims and audio from ");
  });
});
