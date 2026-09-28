// @vitest-environment happy-dom
//
// The note box hands focus back to what had it when it opened. The bug: N in
// Play all, a note, Enter, and space no longer played: focus had nowhere to go,
// and the Play all window only hears keys while focus is inside it.
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { closeIssue, openIssue } from "../src/actions";
import { IssueDialog } from "../src/components/Issues";
import { store } from "../src/store";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe("the note box and focus", () => {
  let root: Root;
  let host: HTMLDivElement;
  let player: HTMLDivElement;
  beforeEach(() => {
    player = document.createElement("div");
    player.tabIndex = -1;
    document.body.appendChild(player);
    host = document.createElement("div");
    document.body.appendChild(host);
    root = createRoot(host);
    store.set({ ep: "ep", pass: "proxy", status: {}, issueDraft: null });
  });
  afterEach(() => {
    act(() => root.unmount());
    host.remove();
    player.remove();
  });

  it("takes focus while open and gives it back when it closes", () => {
    act(() => root.render(createElement(IssueDialog)));
    player.focus();
    expect(document.activeElement).toBe(player);
    act(() => openIssue("sh010", "proxy", 1, "at 1.8 s: "));
    const box = host.querySelector("textarea")!;
    expect(document.activeElement).toBe(box);
    expect(box.selectionStart).toBe("at 1.8 s: ".length);           // typed after the time
    act(() => closeIssue());
    expect(document.activeElement).toBe(player);                     // space plays again
  });
});
