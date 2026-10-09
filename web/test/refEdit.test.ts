// Editing a ref's picture: the form renders for a take and for the live
// picture, and an edited take's record reads back.
import { createElement } from "react";
import { renderToString } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { EditForm, editSourceText } from "../src/components/RefEdit";
import { MOCK_TARGETS } from "../src/mock/mockTargets";
import { initialState, store } from "../src/store";
import type { Ref } from "../src/types";

const PLATE = {
  id: "location:ambulance_bay_night", scope: "series", kind: "location", name: "ambulance bay",
  path: "../refs/_bg/ambulance_bay_night.png", exists: true, sha1: null, used_by: {}, prompt: null,
  override: { fields: [], stale: false }, takes: [], picked: null,
} as unknown as Ref;
const ADA = { ...PLATE, id: "subject:ada", kind: "character", name: "Ada", views: [{ view: "01_threequarter" }] } as unknown as Ref;

describe("edit a ref's picture", () => {
  it("says what it starts from", () => {
    expect(editSourceText({ take: 3, view: "02_side", instruction: "x" })).toBe("t03 (side)");
    expect(editSourceText({ take: null, instruction: "x" })).toBe("the live picture");
  });

  it("the form offers the instruction, the model, other pictures and LoRAs", () => {
    store.set({ ...initialState(), ep: "C:/ep02", targets: MOCK_TARGETS, refs: { "C:/ep02": [PLATE, ADA] } });
    const html = renderToString(createElement(EditForm, { r: PLATE, view: null, take: null, onDone: () => {} }));
    expect(html).toContain("the live picture");
    expect(html).toContain("send as typed");
    expect(html).toContain("Bring in:");
    expect(html).toContain("Ada (character)");          // another ref's picture to bring in
    expect(html).not.toContain(">ambulance bay (location)");  // not itself
    expect(html).toContain("LoRAs");
  });
});
