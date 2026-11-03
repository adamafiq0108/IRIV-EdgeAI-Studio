// Footage deletes that outlive the page they were started on.
//
// Deleting footage is DEFERRED: the rows are hidden immediately and the
// request only goes out when the Undo window closes. Both halves of that used
// to live in the Footage component's own state, which made two things wrong.
//
// Leaving the page during the undo window dropped the suppression: the
// remounted page started with an empty pending set and repainted every image
// the operator had just deleted, while the still-visible Undo button and the
// commit that eventually fired both pointed at setState functions belonging to
// a component that no longer existed.
//
// And the commit cleared the suppression the moment the DELETE returned,
// without waiting for the list refetch it had kicked off. For the few hundred
// milliseconds until that landed, the page held the pre-delete list with
// nothing filtering it -- so every deleted image flashed back for under a
// second and then vanished. That flicker was the whole "did it work?" moment.
//
// So the set lives here, at module scope, and the commit awaits its refetch
// before unhiding. Any mounted Footage subscribes; none of them owns it.
import { api } from "../api/client.js";

const pending = new Set();
const listeners = new Set();
// A new Set per change, cached between them: useSyncExternalStore compares
// snapshots by identity and would loop for ever on a fresh object each read.
let snapshot = new Set();

function publish() {
  snapshot = new Set(pending);
  for (const fn of listeners) fn();
}

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function getPending() {
  return snapshot;
}

/** Hide these ids everywhere, now. Called when Delete is clicked. */
export function hide(ids) {
  for (const id of ids) pending.add(id);
  publish();
}

/** Undo: show them again, nothing was ever sent. */
export function unhide(ids) {
  for (const id of ids) pending.delete(id);
  publish();
}

/**
 * The regret window has closed: actually delete, then stop hiding.
 *
 * `refetch` runs BEFORE the ids leave the pending set, so the page never
 * renders a list that still contains them. It is awaited even when it fails --
 * a refetch that errored leaves the page showing what it had, which is the
 * same footage minus the rows this set is still hiding.
 */
export async function commit(ids, isClip, refetch) {
  try {
    if (isClip) await api.post("/api/detector/clips/delete", { names: ids });
    else await api.post("/api/detector/snapshots/delete", { ids });
  } catch (e) {
    // They still exist, so stop pretending otherwise straight away.
    unhide(ids);
    throw e;
  }
  try {
    await refetch();
  } catch { /* the next poll heals it */ }
  unhide(ids);
}
