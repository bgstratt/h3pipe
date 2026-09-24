// A live file that other episodes read (the `../refs/...` layout), and when
// changing it is worth stopping for.
//
// In a real show nearly every ref is shared, so "this is shared" is not worth a
// dialog on its own. What matters is whose file is live now:
//
//   * your own pick, or no file yet  -> silent. Iterating on your own ref is
//     the common case and must not nag.
//   * another episode's pick         -> confirm. Their pick loses and their
//     takes go stale, but they can re-pick their own take.
//   * nobody's pick (put there by hand, or replaced outside the editor)
//                                    -> confirm, loudly. There is no candidate
//     anywhere to put it back.
//
// Deleting (Clear, or discarding the take that is live) always confirms when
// the file is shared, whoever owns it: it blocks every episode that needs it
// until something is picked again.

import type { Ref } from "../types";

export type SharedAction = "pick" | "clear";

export interface SharedWarning {
  title: string;
  body: string;
  /** the file cannot be got back: nothing has a candidate for it */
  gone: boolean;
  episodes: string[];
}

const list = (eps: string[]): string => {
  if (eps.length === 1) return eps[0];
  if (eps.length === 2) return `${eps[0]} and ${eps[1]}`;
  if (eps.length <= 4) return `${eps.slice(0, -1).join(", ")} and ${eps[eps.length - 1]}`;
  return `${eps.slice(0, 3).join(", ")} and ${eps.length - 3} more`;
};

/** The episodes, besides this one, that read this ref's live file. */
export function sharedWith(r: Pick<Ref, "shared_with">): string[] {
  return r.shared_with ?? [];
}

/** True when this episode's own pick wrote the file that is live now. */
export function ownsLive(r: Pick<Ref, "live_owner">, ep: string | null): boolean {
  const mine = (ep ?? "").replace(/[\\/]+$/, "").split(/[\\/]/).pop() ?? "";
  return !!r.live_owner && r.live_owner === mine;
}

/**
 * What to warn about before `action` changes this ref's live file, or null when
 * there is nothing worth saying. `ep` is the open episode's folder.
 */
export function sharedWarning(
  r: Pick<Ref, "shared_with" | "live_owner" | "exists" | "name">,
  ep: string | null,
  action: SharedAction,
): SharedWarning | null {
  const eps = sharedWith(r);
  if (!eps.length) return null;                       // nothing else reads it
  const mine = ownsLive(r, ep);
  const gone = r.exists && !r.live_owner;
  if (action === "pick") {
    if (!r.exists) return null;                       // nothing to overwrite yet
    if (mine) return null;                            // replacing your own pick
    return {
      title: gone ? "This file has no candidate behind it" : `Replace a file ${list(eps)} read?`,
      body: gone
        ? `${r.name}'s live file was put there outside the editor, so nothing has a copy of it `
          + `to go back to. ${list(eps)} read it, and picking over it loses it for good. `
          + `Their takes will go stale: ref.`
        : `${r.live_owner} picked the file that is live now. Picking here replaces it for `
          + `${list(eps)} too, and their takes go stale: ref — ${r.live_owner} can re-pick its `
          + `own take to get it back.`,
      gone,
      episodes: eps,
    };
  }
  return {
    title: `Delete a file ${list(eps)} read?`,
    body: gone
      ? `${r.name}'s live file was put there outside the editor, so nothing has a copy of it. `
        + `Clearing removes it, and ${list(eps)} are blocked until something is picked.`
      : `Clearing removes the live file, so ${list(eps)} are blocked on it until something is `
        + `picked${r.live_owner ? ` (${r.live_owner} can re-pick its own take)` : ""}.`,
    gone,
    episodes: eps,
  };
}

/** The badge for a row whose live file isn't this episode's own doing. */
export function sharedNote(
  r: Pick<Ref, "shared_with" | "live_owner" | "exists">,
  ep: string | null,
): { label: string; title: string } | null {
  const eps = sharedWith(r);
  if (!eps.length || !r.exists) return null;
  if (ownsLive(r, ep)) return null;
  if (!r.live_owner) {
    return {
      label: "not from here",
      title: `The live file was put there outside the editor (no candidate behind it). `
             + `Also read by ${list(eps)}.`,
    };
  }
  return {
    label: `from ${r.live_owner}`,
    title: `${r.live_owner}'s pick wrote the file that is live now. Also read by ${list(eps)}.`,
  };
}
