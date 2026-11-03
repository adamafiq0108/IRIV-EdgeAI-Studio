// Clear events on the Events Log: the words and the one round trip, kept out
// of the page so they can be checked without a browser (tests/
// check_eventsclear.mjs) and so EventTable.jsx stays the table it is.
//
// WHAT THE PERSON MUST KNOW BEFORE SAYING YES. The route (core/routes/
// events.py clear_events) deletes EVERY stored event of this system, not the
// filtered rows and not the page on screen. With a filter set, the header's
// number is how many MATCH, so the dialog says "not only the N that match"
// rather than quoting a count that is smaller than what goes. Footage and
// dataset rows are kept, and the exports are the only way to keep a copy.
//
// "0 cleared" only ever means nothing was there: a database the master could
// not reach comes back as an error (503) and lands on the error toast.

/** The confirm() options for Clear events. `total`/`capped` are the page
 *  header's count ("20000+"), `dirty` whether a filter narrows it. */
export function clearConfirm({ total = 0, capped = false, dirty = false } = {}) {
  const n = `${total}${capped ? "+" : ""}`;
  const what = dirty
    ? `Every event of this system, not only the ${n} that match these filters,`
    : `All ${n} event${total === 1 && !capped ? "" : "s"} of this system`;
  return {
    title: "Clear every event?",
    description: `${what} will be deleted for good. This cannot be undone. `
      + "Dataset items, clips and snapshots are NOT deleted. Use Export CSV "
      + "or Export LOG first if you may need them.",
    destructive: true,
    confirmLabel: "Clear events",
  };
}

/** The success toast: what the server says it removed, not what was shown. */
export const clearedText = (n) => `${n} event${n === 1 ? "" : "s"} cleared`;

/** Nothing to clear: the unfiltered header reads 0. A filtered 0 still may
 *  hide events outside the filter, so it does not disable the button. */
export const nothingToClear = ({ total = 0, dirty = false } = {}) => !dirty && !total;

/** Ask, clear, report. `post` is the call itself (the component's
 *  api.post("/api/events/clear"), so the path stays where check_basepath
 *  looks for it). Resolves to the number removed, or null when the person
 *  said no or the server refused (the toast has said why). */
export async function clearEvents({ post, confirm, toast, total, capped, dirty,
                                    onCleared }) {
  if (!await confirm(clearConfirm({ total, capped, dirty }))) return null;
  try {
    const d = await post();
    const n = Number(d?.removed) || 0;
    onCleared?.(n);
    toast.success(clearedText(n));
    return n;
  } catch (e) {
    toast.error("Could not clear the events", e?.message);
    return null;
  }
}
