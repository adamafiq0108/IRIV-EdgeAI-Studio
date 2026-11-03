// Clear events on the Events Log (owner request, 2026-10-11): the dialog, the
// round trip and the page wiring. The route itself is smoke_eventsclear.py.
//
// Pinned:
//   1. the confirm is destructive, quotes the header's count ("20000+" when
//      capped), says every event of THIS system goes for good, that it cannot
//      be undone, that dataset items, clips and snapshots are NOT deleted,
//      and to Export CSV or Export LOG first
//   2. with a filter set it does not pass the matching count off as what
//      goes: "not only the N that match"
//   3. No in the dialog sends nothing; Yes posts once,
//      hands the server's count to the page and toasts "N events cleared"
//   4. a refusal (503 database down, 403) lands on the error toast and the
//      page is NOT emptied
//   5. the button posts /api/events/clear through the api client, is drawn
//      only when the query answer's can_clear is true,
//      sits beside Clear filters, and a successful clear empties the table,
//      resets the count and the page, and asks again (rows + filter values)
//
// Plain node, no node_modules: the logic is lib/eventsClear.js, the rest is
// read from the source.
//
//     node tests/check_eventsclear.mjs
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { readText } from "./_text.mjs";

const REPO = join(dirname(fileURLToPath(import.meta.url)), "..");
const SRC = join(REPO, "frontend", "src");
let bad = 0;

function check(label, ok, detail = "") {
  console.log((ok ? "ok   " : "FAIL ") + label + (ok ? "" : "\n      " + detail));
  if (!ok) bad += 1;
}

const lib = await import(pathToFileURL(join(SRC, "lib", "eventsClear.js")).href);

// -- 1. the dialog -------------------------------------------------------------
const c = lib.clearConfirm({ total: 20000, capped: true, dirty: false });
check("the confirm is destructive with its own label",
      c.destructive === true && c.confirmLabel === "Clear events", JSON.stringify(c));
check("...quotes the header's capped count", c.description.includes("20000+ events"),
      c.description);
for (const [what, needle] of [
  ["every event of this system", "of this system"],
  ["for good", "deleted for good"],
  ["cannot be undone", "cannot be undone"],
  ["dataset items, clips and snapshots are kept", "Dataset items, clips and snapshots are NOT deleted"],
  ["export first", "Export CSV or Export LOG first"],
]) check(`...says ${what}`, c.description.includes(needle), c.description);
check("one event reads singular",
      lib.clearConfirm({ total: 1 }).description.startsWith("All 1 event of"));

// -- 2. filtered ------------------------------------------------------------------
const f = lib.clearConfirm({ total: 12, dirty: true });
check("filtered: every event, not only the matching 12",
      f.description.includes("not only the 12 that match"), f.description);
check("an unfiltered empty log has nothing to clear; a filtered 0 still may",
      lib.nothingToClear({ total: 0, dirty: false })
      && !lib.nothingToClear({ total: 0, dirty: true })
      && !lib.nothingToClear({ total: 3, dirty: false }));

// -- 3/4. the round trip -------------------------------------------------------
function rig({ yes = true, answer = { ok: true, removed: 7 }, fail = null } = {}) {
  const r = { posts: [], toasts: [], cleared: [], asked: [] };
  r.args = {
    total: 7, capped: false, dirty: false,
    confirm: async (o) => { r.asked.push(o); return yes; },
    post: async () => {
      r.posts.push("post");
      if (fail) throw fail;
      return answer;
    },
    toast: { success: (t) => r.toasts.push(["success", t]),
             error: (t, m) => r.toasts.push(["error", t, m]) },
    onCleared: (n) => r.cleared.push(n),
  };
  return r;
}

let r = rig({ yes: false });
check("No sends nothing", (await lib.clearEvents(r.args)) === null
      && r.posts.length === 0 && r.cleared.length === 0 && r.asked.length === 1);

r = rig();
const n = await lib.clearEvents(r.args);
check("Yes posts once", r.posts.length === 1, JSON.stringify(r.posts));
check("...hands the SERVER's count to the page", n === 7 && r.cleared[0] === 7,
      JSON.stringify(r.cleared));
check("...and toasts \"7 events cleared\"",
      r.toasts.length === 1 && r.toasts[0][0] === "success"
      && r.toasts[0][1] === "7 events cleared", JSON.stringify(r.toasts));
check("one event cleared reads singular", lib.clearedText(1) === "1 event cleared");

const down = Object.assign(new Error("The database is unavailable, so stored "
  + "history cannot be read or changed. Live detection is unaffected."), { status: 503 });
r = rig({ fail: down });
check("database down: the error toast carries the server's sentence",
      (await lib.clearEvents(r.args)) === null
      && r.toasts.length === 1 && r.toasts[0][0] === "error"
      && r.toasts[0][2] === down.message, JSON.stringify(r.toasts));
check("...and the page is NOT emptied", r.cleared.length === 0);

// -- 5. the page wiring --------------------------------------------------------
const btn = readText(join(SRC, "components", "ClearEventsButton.jsx"));
const page = readText(join(SRC, "pages", "EventTable.jsx"));
check("the button draws nothing unless allowed", /if \(!allowed\) return null;/.test(btn));
check("...posts /api/events/clear through the api client",
      btn.includes('post: () => api.post("/api/events/clear", {})'));
check("...uses the shared destructive confirm and the toast",
      btn.includes("useConfirm()") && btn.includes("useToast()")
      && btn.includes("clearEvents({"));
check("...and looks destructive", btn.includes('className="btn btn-sm btn-danger"'));
check("allowed is the query answer's can_clear",
      page.includes("setCanClear(!!d.can_clear);")
      && page.includes("<ClearEventsButton allowed={canClear}"));
check("it sits beside Clear filters",
      /Clear filters\n\s*<\/button>\n\s*<ClearEventsButton /.test(page));
const done = (page.match(/function cleared\(\) \{([\s\S]*?)\n  \}/) || [])[1] || "";
check("success empties the table, resets count and page, and asks again",
      ["newest.current = [];", "setView([]);", "setTotal(0);", "setCapped(false);",
       "setHeld(0);", "setPage(0);", "loadAttrs();", "load();"]
        .every((s) => done.includes(s)), done);
check("the filter values are re-read after a clear",
      page.includes("const loadAttrs = useCallback(") && page.includes("useEffect(loadAttrs, [loadAttrs]);"));

console.log(bad ? `\n${bad} FAILED` : "\nOK");
process.exit(bad ? 1 : 0);
