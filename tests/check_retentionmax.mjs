// Data Retention: the most each row may be is shown beside its field, and a
// number above it is refused before the save (Tok Aba 2026-10-05).
//
// Without the data on the NVMe the three day rows say "up to 60 days"; with
// it they say "up to 12 months (365 days)"; the months rows say "up to 12
// months" either way. Typing past the max shows the reason beside the field
// and holds the Save button; a saved value above the cap (kept as saved, the
// cap applied) arrives as a one-time notice, which the page says out loud,
// and the row says the saved period comes back with the NVMe. The NVMe-not-
// mounted alarm shows as a banner. The server side of the same rules is
// tests/smoke_retentioncap.py and tests/smoke_nvmeunmount.py.
//
//   node tests/check_retentionmax.mjs
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".retentionmax-test.cjs");

const dom = new JSDOM("<!doctype html><html><body></body></html>",
                      { pretendToBeVisual: true, url: "https://localhost/" });
global.window = dom.window;
global.document = dom.window.document;
Object.defineProperty(global, "navigator",
                      { value: dom.window.navigator, configurable: true });
for (const k of ["HTMLElement", "HTMLInputElement", "Element", "Node",
                 "SVGElement", "Image", "Event", "CustomEvent", "MouseEvent",
                 "requestAnimationFrame", "cancelAnimationFrame",
                 "getComputedStyle", "localStorage"]) {
  global[k] = dom.window[k];
}
global.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
global.IntersectionObserver = class {
  observe() {} unobserve() {} disconnect() {} takeRecords() { return []; }
};
global.IS_REACT_ACT_ENVIRONMENT = true;

let bad = 0;
const check = (msg, passed, detail = "") => {
  if (passed) console.log(`ok   ${msg}`);
  else { console.error(`FAIL ${msg}${detail ? `\n      ${detail}` : ""}`); bad += 1; }
};

// The api client, under the test's control: GET /api/hub/retention answers
// what the test sets, every other GET answers an empty hub.
const shimApi = {
  name: "shim-api",
  setup(b) {
    b.onResolve({ filter: /api\/client\.js$/ }, () => (
      { path: "api-shim", namespace: "shim" }));
    b.onLoad({ filter: /^api-shim$/, namespace: "shim" }, () => ({
      contents: `
        globalThis.__posts = [];
        export const api = {
          get(url) {
            if (url === "/api/hub/retention")
              return Promise.resolve(JSON.parse(JSON.stringify(globalThis.__ret)));
            return Promise.resolve({ cards: globalThis.__cards || [] });
          },
          post(url, body) { globalThis.__posts.push([url, body]);
                            return Promise.resolve(JSON.parse(JSON.stringify(globalThis.__ret))); },
          put() { return Promise.resolve({}); },
        };
        export function friendlyError(e) { return String(e && e.message || e); }
        export function saveBlob() {}
      `,
      resolveDir: FRONT, loader: "js",
    }));
  },
};

const p = (rel) => join(FRONT, rel).replace(/\\/g, "/");
const ENTRY = `
import { createRoot } from "react-dom/client";
import { act, createElement as h } from "react";
import RetentionPanel from "${p("src/components/RetentionPanel.jsx")}";
import { ToastProvider } from "${p("src/components/ui/Toast.jsx")}";
export { act, h, RetentionPanel, ToastProvider };
export function mount(el, node) {
  const root = createRoot(el);
  act(() => { root.render(node); });
  return { unmount: () => act(() => root.unmount()) };
}
`;
await build({
  plugins: [shimApi],
  stdin: { contents: ENTRY, resolveDir: FRONT, loader: "js" },
  bundle: true, format: "cjs", platform: "node", outfile: OUT,
  jsx: "automatic", logLevel: "silent",
  alias: { "@": join(FRONT, "src") },
  loader: { ".css": "empty" },
  define: { "process.env.NODE_ENV": '"development"' },
});
const m = req(OUT);
const { h } = m;
const settle = () => m.act(async () => { await new Promise((r) => setTimeout(r, 30)); });

function answer(cap, notice = []) {
  const pol = { events_days: 45, snapshots_days: 45, clips_days: 45,
                history_months: 12, events_months: 12 };
  return {
    ok: true, policy: pol, notice,
    limits: { events_days: cap, snapshots_days: cap, clips_days: cap,
              history_months: 12, events_months: 12 },
    storage: { extended: cap === 365, limit_days: cap, min_days: cap,
               differs: false,
               reason: cap === 365 ? "This box's data is on the NVMe: up to 12 months."
                 : "This box's data is on the built-in storage: up to 60 days. "
                   + "Longer retention needs an NVMe drive with the data moved "
                   + "to it (up to 12 months)." },
    systems: [],
    schedule: { starts_at: 0, starts_at_set: false, due: {}, first_deletion: {},
                cutoffs: {}, next_at: null, last_at: null, last_deleted: 0 },
  };
}

async function render(ret, fn) {
  globalThis.__ret = ret;
  globalThis.__posts = [];
  const box = document.createElement("div");
  document.body.appendChild(box);
  const r = m.mount(box, h(m.ToastProvider, null, h(m.RetentionPanel)));
  await settle();
  try { return await fn(box); } finally { r.unmount(); document.body.removeChild(box); }
}
const maxOf = (box, key) =>
  box.querySelector(`[data-retention-max="${key}"]`)?.textContent || "";
const saveBtn = (box) => [...box.querySelectorAll("button")]
  .find((b) => b.textContent.includes("Save Retention Policy"));
function type(input, value) {
  const set = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype, "value").set;
  m.act(() => {
    set.call(input, value);
    input.dispatchEvent(new window.Event("input", { bubbles: true }));
  });
}

await render(answer(60), async (box) => {
  check("without the data on the NVMe: the day rows say up to 60 days",
        ["events_days", "snapshots_days", "clips_days"]
          .every((k) => maxOf(box, k) === "Maximum retention period: 60 days"),
        ["events_days", "snapshots_days", "clips_days"].map((k) => maxOf(box, k)));
  check("...the months rows say up to 12 months",
        maxOf(box, "history_months") === "Maximum retention period: 12 months"
        && maxOf(box, "events_months") === "Maximum retention period: 12 months");
  // The "why only 60 days" sentence is a notice in the bell now
  // (NotificationCenter, Important); the page no longer repeats it.
  check("...and the page itself no longer carries the box line (the bell does)",
        !box.querySelector("[data-retention-limit]")
        && !box.textContent.includes("for events, snapshots and clips."));
  check("the Save button starts enabled", saveBtn(box) && !saveBtn(box).disabled);
  const row = box.querySelector('[data-retention-max="snapshots_days"]')
    .closest("tr");
  const input = row.querySelector('input[type="number"]');
  check("the field itself carries the max", input.getAttribute("max") === "60",
        input.outerHTML);
  type(input, "61");
  await settle();
  check("61 is refused beside the field",
        box.querySelector('[data-retention-over="snapshots_days"]')?.textContent
          === "More than this box allows: up to 60 days.",
        row.textContent);
  check("...and the Save waits", saveBtn(box).disabled === true);
  type(input, "60");
  await settle();
  check("back at 60, the refusal goes and Save returns",
        !box.querySelector("[data-retention-over]") && !saveBtn(box).disabled);
});

await render(answer(365), async (box) => {
  check("with the data on the NVMe: the day rows say up to 12 months (365 days)",
        maxOf(box, "clips_days") === "Maximum retention period: 12 months (365 days)",
        maxOf(box, "clips_days"));
  check("...and there is no box line at all (nothing left to explain)",
        !box.querySelector("[data-retention-limit]")
        && !box.textContent.includes("for events, snapshots and clips."));
});

// The sentence now lives in the bell: shown only while some data is not on the
// NVMe, worded by whether a drive is fitted, with Configure Storage only then.
const bell = readFileSync(join(ROOT, "frontend/src/components/NotificationCenter.jsx"), "utf8");
check("the bell's notice shows only while data is not all on the NVMe",
      bell.includes("storage && (!storage.extended || storage.differs)"));
check("...says whether a drive is fitted, and offers Configure Storage only then",
      bell.includes("Data is not on the NVMe: kept up to")
      && bell.includes("No NVMe drive: data is kept up to")
      && bell.includes("storage.nvme_fitted")
      && bell.includes('action: "Configure Storage"'));

const NOTE = "Video clips: kept for 60 days while the data is on the built-in "
  + "storage. The saved 12 months applies again when the data is back on the "
  + "NVMe drive.";
const ALARM = "The NVMe drive is not mounted. New data is being saved on the "
  + "built-in storage and kept for 60 days. Mount the drive again (or restart "
  + "the box) to go back to 12 months.";
const away = { ...answer(60, [NOTE]),
  saved: { events_days: 45, snapshots_days: 45, clips_days: 365,
           history_months: 12, events_months: 12 },
  alarm: { active: true, text: ALARM } };
await render(away, async (box) => {
  check("a saved value above the cap is said once, out loud",
        document.body.textContent.includes(NOTE)
        && document.body.textContent.includes("Kept for 60 days for now"),
        document.body.textContent.slice(0, 300));
  check("...the row says the saved 12 months comes back with the NVMe",
        box.querySelector('[data-retention-saved="clips_days"]')?.textContent
          === "Saved: 12 months (365 days). Applies again when the data is "
            + "back on the NVMe drive.",
        box.querySelector('[data-retention-saved="clips_days"]')?.textContent);
  check("...only on that row",
        box.querySelectorAll("[data-retention-saved]").length === 1);
  check("the drive-not-mounted banner is shown, in the agreed words",
        box.querySelector("[data-storage-alarm]")?.textContent === ALARM,
        box.querySelector("[data-storage-alarm]")?.textContent);
  // The way out (bug 8): a confirmed click posts the forget; the click alone
  // posts nothing.
  const forget = box.querySelector("[data-testid=nvme-forget]");
  check("...with the admin's way out beside it, in plain words",
        forget?.textContent.includes("This box no longer uses the NVMe drive"),
        forget?.textContent);
  await m.act(async () => {
    forget.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  });
  await settle();
  check("the click asks first and posts nothing by itself",
        globalThis.__posts.length === 0
        && document.body.textContent.includes("This box no longer uses the NVMe drive?"),
        document.body.textContent.slice(0, 400));
  globalThis.__ret = { ...away, alarm: { active: false, text: "" },
                       forgotten: ["/mnt/nvme"], still_expected: {} };
  const confirmBtn = [...document.body.querySelectorAll("button")]
    .find((b) => b.textContent.trim() === "No longer uses the drive");
  await m.act(async () => {
    confirmBtn?.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  });
  await settle();
  check("confirmed: one POST to the forget route, and the banner goes",
        globalThis.__posts.length === 1
        && globalThis.__posts[0][0] === "/api/hub/retention/nvme/forget"
        && !box.querySelector("[data-storage-alarm]"),
        JSON.stringify(globalThis.__posts));
});
await render(answer(365), async (box) => {
  check("no alarm, no saved line when nothing is missing",
        !box.querySelector("[data-storage-alarm]")
        && !box.querySelector("[data-retention-saved]")
        && !box.querySelector("[data-testid=nvme-forget]"));
});

// The customer copy (2026-10-11): the spec's words where the code makes them
// true, the code's facts where it does not. Saving sweeps on the spot, so it
// is confirmed first; the hold is 3 s; "Delete All System Data" says what it
// leaves; Trash reads "Unused Data".
const click = (el) => m.act(async () => {
  el.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
});
const button = (label) => [...document.body.querySelectorAll("button")]
  .find((b) => b.textContent.trim() === label);
globalThis.__cards = [{ slug: "line1", name: "Line 1", url: "http://x:9001" }];
await render({ ...answer(60), schedule: { ...answer(60).schedule,
  next_at: Date.now() / 1000 + 4 * 3600, last_at: Date.now() / 1000 - 600,
  last_deleted: 7, interval_s: 21600 } }, async (box) => {
  const t = box.textContent.replace(/\s+/g, " ");
  check("the page says one policy for every system on this device",
        t.includes("These settings apply to every system on this device.")
        && t.includes("One policy applies to all systems on this device."));
  check("Storage Capacity says 60 days built-in and 12 months on NVMe, not 12 alone",
        box.querySelector("[data-retention-capacity]")?.textContent.includes(
          "up to 60 days on the built-in storage, or up to 12 months when the data is on an NVMe drive"));
  check("the date is Policy Effective From, with the real calculation",
        t.includes("Policy Effective From") && t.includes("Next Scheduled Deletion")
        && box.querySelector("[data-retention-from-help]")?.textContent
          .includes("Saving a policy starts it from today unless you pick an earlier date"));
  const sched = box.querySelector("[data-retention-schedule]")?.textContent || "";
  check("the cleanup status shows the three values the API returned",
        sched.includes("Next cleanup: in 4 h") && sched.includes("Last cleanup:")
        && sched.includes("Files deleted during last cleanup: 7")
        && sched.includes("every 6 hours"), sched);
  check("Data Not Affected by Retention lists the five verified items",
        box.querySelector("[data-retention-unaffected]")?.textContent
          === "User accountsActivity Log (audit log)System configurationTemplatesWorkflows");
  check("the warning says the hold lasts 3 seconds",
        box.querySelector("[data-retention-warning]")?.textContent.includes(
          "press and hold the confirmation control for 3 seconds"));
  check("the row offers Choose Data to Delete and Delete All System Data",
        t.includes("Choose Data to Delete") && !!button("Delete All System Data")
        && !t.includes("Selected deletion"));
  check("Trash is Unused Data, with its empty state",
        t.includes("Unused Data") && t.includes("No unused data found")
        && !/\bTrash\b/.test(t)
        && !!box.querySelector('[aria-label="Refresh unused data"]'));

  await click(saveBtn(box));
  await settle();
  check("Save Retention Policy asks first and posts nothing by itself",
        globalThis.__posts.length === 0
        && document.body.textContent.includes("Save retention policy?")
        && document.body.textContent.includes("Saving sets Policy Effective From to today"));
  await click(button("Save Policy"));
  await settle();
  check("...Save Policy then posts the policy once",
        globalThis.__posts.length === 1
        && globalThis.__posts[0][0] === "/api/hub/retention",
        JSON.stringify(globalThis.__posts));

  await click(button("Delete All System Data"));
  await settle();
  const dlg = document.body.textContent;
  check("Delete All System Data names the system and what it keeps",
        dlg.includes("Delete all recorded data for this system?")
        && dlg.includes("all recorded data for Line 1")
        && dlg.includes("the system's logs are kept")
        && dlg.includes("workflows and user accounts are not deleted")
        && !!button("Hold to Delete") && globalThis.__posts.length === 1);
});
globalThis.__cards = undefined;

// The effective date is stamped once (retention.set_policy, 2026-10-11): on a
// box with a stored date, a save without a picked date KEEPS it, and the help
// text and the Save confirmation say so, naming the date. No starts_at is
// posted unless the date was picked.
const SET = Math.floor(new Date(2026, 2, 3, 12).getTime() / 1000);
await render({ ...answer(60), schedule: { ...answer(60).schedule,
  starts_at: SET, starts_at_set: true } }, async (box) => {
  check("a stored date: the help says a save without a date keeps it",
        box.querySelector("[data-retention-from-help]")?.textContent
          .includes("Saving without picking a date keeps this date.")
        && !box.querySelector("[data-retention-from-help]")?.textContent.includes("sets it to today"));
  await click(saveBtn(box));
  await settle();
  const t = document.body.textContent;
  const shown = new Date(SET * 1000).toLocaleDateString(undefined,
    { day: "numeric", month: "short", year: "numeric" });
  check("...the Save confirmation keeps the stored date, by name",
        t.includes(`Saving keeps Policy Effective From at ${shown}`)
        && !t.includes("Saving sets Policy Effective From to today"), t.slice(-600));
  await click(button("Save Policy"));
  await settle();
  check("...and the post carries no starts_at (nothing was picked)",
        globalThis.__posts.length === 1 && !("starts_at" in globalThis.__posts[0][1]),
        JSON.stringify(globalThis.__posts));
});

if (bad) {
  console.error(`\n${bad} check(s) failed`);
  process.exit(1);
}
console.log("\nall retention max checks passed");
dom.window.close();
process.exit(0);
