// An audit entry's `detail` must never reach React as an object.
//
// It is a JSONB column, so it arrives as one -- `{}` for most actions, which
// is TRUTHY, so `{e.detail ? <span>{e.detail}</span> : null}` passed an empty
// object straight to React and took the entire Account Management page down
// with "objects are not valid as a React child". Every row in the log has it,
// so the tab could not be opened at all.
//
// A build catches none of this: the JSX is valid, the module resolves, and the
// value's SHAPE is only known at runtime. So this exercises the formatter
// against the shapes the log actually stores.
//
//   node tests/check_activity.mjs
import { readFileSync } from "node:fs";
import { readText } from "./_text.mjs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
// Imported directly, no bundler: the formatter lives in its own module with no
// imports precisely so this test needs nothing but node.
const MOD = join(FRONT, "src", "components", "auditDetail.js");
const { detailText } = await import(pathToFileURL(MOD).href);

function eq(got, want, what) {
  if (got !== want) {
    console.error(`FAIL ${what}\n  got:  ${JSON.stringify(got)}\n  want: ${JSON.stringify(want)}`);
    process.exit(1);
  }
  console.log(`== ${what}`);
}

// THE crash: every action that records no detail stores {}.
eq(detailText({}), "", "an empty object renders as nothing, not as {}");
eq(detailText(null), "", "null renders as nothing");
eq(detailText(undefined), "", "a missing detail renders as nothing");

// A string detail (what auth's own records pass) survives verbatim.
eq(detailText("now adam"), "now adam", "a string detail is kept as it is");

// Every key is shown. A summary that drops one is a log you cannot trust on
// the occasion it mattered.
eq(detailText({ slug: "demo", rows: 12 }), "slug: demo · rows: 12",
   "every key of an object detail is shown");
eq(detailText({ nested: { a: 1 } }), 'nested: {"a":1}',
   "a nested value is shown as JSON rather than [object Object]");
eq(detailText([{ a: 1 }, "x"]), 'a: 1, x', "a list of details is joined");

// Whatever the shape, the result is a string -- that is the property that
// keeps this from being a page crash again.
for (const shape of [{}, [], 0, false, "", { a: {} }, [[]], new Date(0)]) {
  if (typeof detailText(shape) !== "string") {
    console.error(`FAIL non-string for ${JSON.stringify(shape)}`);
    process.exit(1);
  }
}
console.log("== every shape returns a string");

// ...and the page actually goes through it. A formatter nothing calls is a
// formatter that does not stop the crash it was written for.
const PANEL = readText(join(FRONT, "src", "components", "AccountsPanel.jsx"));
if (!PANEL.includes("detailText(e.detail)") || /\{\s*e\.detail\s*\}/.test(PANEL)) {
  console.error("FAIL AccountsPanel renders e.detail without formatting it");
  process.exit(1);
}
console.log("== the activity list renders detail through it");

// -- the customer names for actions (components/auditActions.js) ------------
// The ids are the server's; the names are this page's. The two switches are
// NOT actions of their own: core/routes/auth.py writes them as
// system.settings with the detail below, so that is the shape tested.
const ACT = await import(pathToFileURL(
  join(FRONT, "src", "components", "auditActions.js")).href);
const { actionLabel, actionDetail, resultLabel, whenText } = ACT;
eq(actionLabel({ action: "login" }), "Signed in", "login reads Signed in");
eq(actionLabel({ action: "login", result: "fail" }), "Sign-in failed",
   "a failed login reads Sign-in failed");
eq(actionLabel({ action: "logout" }), "Signed out", "logout reads Signed out");
eq(actionLabel({ action: "system.settings", detail: "something else" }),
   "System setting changed", "an unnamed system.settings change is generic");
eq(actionLabel({ action: "system.settings", detail: "hailo load balancer on" }),
   "AI Processing Balance enabled", "the balancer switch on");
eq(actionLabel({ action: "system.settings", detail: "hailo load balancer off" }),
   "AI Processing Balance disabled", "the balancer switch off");
eq(actionLabel({ action: "system.settings", detail: "shared poll cache on" }),
   "Shared Dashboard Cache enabled", "the poll cache switch on");
eq(actionLabel({ action: "system.settings", detail: "shared poll cache off" }),
   "Shared Dashboard Cache disabled", "the poll cache switch off");
eq(actionDetail({ action: "system.settings", detail: "shared poll cache on" },
                "shared poll cache on"), "",
   "...and the internal words are not repeated beside the name");
eq(actionLabel({ action: "video.overlay" }), "Video box drawing changed",
   "video.overlay is the Draw boxes setter");
eq(actionDetail({ action: "video.overlay" }, "cam1: boxes drawn client -> server",
                (s) => (s === "cam1" ? "Front Gate" : s)),
   "Front Gate: Draw boxes in the browser -> Embed boxes in the video",
   "...named by the system's name and the choice's own words");
// Registered sign-in devices (core/services/auth.py): the owner's words.
eq(actionLabel({ action: "device.registered" }), "Device registered",
   "device.registered reads Device registered");
eq(actionLabel({ action: "device.removed" }), "Device removed",
   "device.removed reads Device removed");
eq(actionLabel({ action: "device.expired" }), "Device expired (inactive for 30 days)",
   "device.expired says why it went");
eq(actionLabel({ action: "device.remove_others" }), "Other devices removed",
   "device.remove_others reads Other devices removed");
eq(actionLabel({ action: "device.refused" }), "Sign-in refused (device limit reached)",
   "device.refused says what was refused and why");
// An action this page has never heard of: readable, never the dotted id,
// never a throw -- the server will add ids this file does not know yet.
eq(actionLabel({ action: "storage.nvme_forget_all" }), "Storage nvme forget all",
   "an unknown action falls back to readable words");
for (const shape of [null, undefined, {}, { action: 7 }, { action: "" }]) {
  const got = actionLabel(shape);
  if (typeof got !== "string" || !got || /[._]/.test(got)) {
    console.error(`FAIL unreadable label for ${JSON.stringify(shape)}: ${got}`);
    process.exit(1);
  }
}
console.log("== no row shape crashes the label or leaks an id");
eq(resultLabel("fail"), "Failed", "a failed result reads Failed");
eq(resultLabel("ok"), "", "an ok result shows no badge");
eq(whenText("not a time"), "not a time", "an unreadable stamp is kept as it was");
if (!/2026/.test(whenText("2026-10-11T03:52:00+00:00"))) {
  console.error("FAIL a real stamp is not formatted as a date");
  process.exit(1);
}
console.log("== a real stamp becomes a local date and time");

// -- the page uses them, and says what the spec says ---------------------------
for (const [needle, what] of [
  ["actionLabel(e)", "the row name goes through the mapping"],
  ["whenText(e.ts)", "the time is a readable local one"],
  ["dev ? <code", "only a developer sees the raw id"],
  [">Activity Log<", "the tab is called Activity Log"],
  [">Login Sessions<", "the sessions tab is called Login Sessions"],
  ["Sign Out Other Sessions", "the bulk action is Sign Out Other Sessions"],
  ["Sign out this session?", "a single sign-out is confirmed"],
  ["Sign out other sessions?", "the bulk sign-out is confirmed"],
  ["Current Session", "the current session is labelled"],
  ["This device", "the browser in use is labelled"],
  ["Remove Device", "a device can be removed"],
  ["Remove this device?", "removing a device is confirmed"],
  ["Remove Other Devices", "the bulk device removal is offered"],
  ["Remove other devices?", "...and confirmed"],
  ["Registered devices: ${n} of ${limit}", "the per-account counter"],
  ["has not been active for ${days} days is removed automatically.",
   "the 30-day rule is stated"],
  ["No activity recorded", "the log has its empty state"],
  ["No users yet", "the user list has its empty state"],
  ["No custom roles", "the roles tab has its empty state"],
  ["Delete this user?", "deleting a user is confirmed in the spec's words"],
  ["Remove this email address?", "removing an address is confirmed"],
]) {
  if (!PANEL.includes(needle)) {
    console.error(`FAIL ${what}: "${needle}" not in AccountsPanel.jsx`);
    process.exit(1);
  }
  console.log(`== ${what}`);
}
// The words the copy pass retired, and the promises section 7 found untrue.
for (const gone of ["All Activities", "                Revoke\n",
                    "may remain signed in", "define a set of permissions"]) {
  if (PANEL.includes(gone)) {
    console.error(`FAIL "${gone.trim()}" is back in AccountsPanel.jsx`);
    process.exit(1);
  }
}
console.log("== the retired words and the untrue promises are gone");
console.log("ALL OK");
