// Settings > System: the Auto Logout heading and its switch say the same thing.
//
// Beta feedback: the setting was headed Auto Logout while the words beside
// its switch talked about staying signed in, so nobody could tell which way
// the switch went. Wording only -- the stored flag keeps its meaning:
// auto_logout true = the idle timeout signs sessions out, false = a 30-day
// window. Pinned: the heading, the switch label (visible and aria), the on
// and off sentences, the new-sign-ins note, and that switching ON still sends
// auto_logout: true.
//
// Customer copy pass (2026-10-11) also pinned here: the page heading and
// subtitle, the Email card (real switch when email is on; "Email Recovery
// Unavailable" + the Password Recovery empty state when off, never a second
// switch), and the two testing cards (Hailo balancer, poll cache) shown to the
// developer tier only.
//
//   node tests/check_autologout.mjs
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".autologout-test.cjs");

const dom = new JSDOM("<!doctype html><html><body></body></html>",
                      { pretendToBeVisual: true, url: "https://localhost/" });
global.window = dom.window;
global.document = dom.window.document;
Object.defineProperty(global, "navigator",
                      { value: dom.window.navigator, configurable: true });
for (const k of ["HTMLElement", "Element", "Node", "SVGElement", "Image",
                 "Event", "CustomEvent", "MouseEvent",
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

// The api client, under the test's control: GET answers the settings the
// test sets, POST records what the switch sends.
const shimApi2 = {
  name: "shim-api",
  setup(b) {
    b.onResolve({ filter: /api\/client\.js$/ }, () => (
      { path: "api-shim", namespace: "shim" }));
    b.onLoad({ filter: /^api-shim$/, namespace: "shim" }, () => ({
      contents: `
        globalThis.__posts = [];
        export const api = {
          get() { return Promise.resolve({ ...globalThis.__settings }); },
          post(url, body) { globalThis.__posts.push([url, body]);
                            return Promise.resolve({ ...globalThis.__settings, ...body }); },
          put() { return Promise.resolve({}); },
        };
        export function friendlyError(e) { return String(e && e.message || e); }
        export function saveBlob() {}
      `,
      resolveDir: FRONT, loader: "js",
    }));
  },
};

// The signed-in user, under the test's control (the developer-tier check).
const shimApp = {
  name: "shim-app",
  setup(b) {
    b.onResolve({ filter: /state\/app\.jsx$/ }, () => (
      { path: "app-shim", namespace: "shim" }));
    b.onLoad({ filter: /^app-shim$/, namespace: "shim" }, () => ({
      contents: `export function useApp() {
        return { user: globalThis.__user }; }`,
      resolveDir: FRONT, loader: "js",
    }));
  },
};

const p = (rel) => join(FRONT, rel).replace(/\\/g, "/");
const ENTRY = `
import { createRoot } from "react-dom/client";
import { act, createElement as h } from "react";
import SystemPanel from "${p("src/components/SystemPanel.jsx")}";
import { ToastProvider } from "${p("src/components/ui/Toast.jsx")}";
export { act, h, SystemPanel, ToastProvider };
export function mount(el, node) {
  const root = createRoot(el);
  act(() => { root.render(node); });
  return { unmount: () => act(() => root.unmount()) };
}
`;
await build({
  plugins: [shimApi2, shimApp],
  stdin: { contents: ENTRY, resolveDir: FRONT, loader: "js" },
  bundle: true, format: "cjs", platform: "node", outfile: OUT,
  jsx: "automatic", logLevel: "silent",
  alias: { "@": join(FRONT, "src") },
  loader: { ".css": "empty" },
  define: { "process.env.NODE_ENV": '"development"' },
});
const m = req(OUT);
const { h } = m;
const settle = () => m.act(async () => { await new Promise((r) => setTimeout(r, 20)); });

async function render(settings, fn, user = { is_developer: false }) {
  globalThis.__settings = settings;
  globalThis.__user = user;
  globalThis.__posts = [];
  const box = document.createElement("div");
  document.body.appendChild(box);
  const r = m.mount(box, h(m.ToastProvider, null, h(m.SystemPanel)));
  await settle();
  try { return await fn(box); } finally { r.unmount(); document.body.removeChild(box); }
}
const section = (box) => [...box.querySelectorAll("section")]
  .find((s) => s.textContent.includes("Automatic Sign-out"));

await render({ auto_logout: false, idle_minutes: 43200, email: true,
               panel_reset: true }, async (box) => {
  const s = section(box);
  const sw = s?.querySelector('[role="switch"], button[aria-checked], input[type="checkbox"]');
  check("the setting is named Automatic Sign-out", !!s, box.textContent.slice(0, 200));
  // Since 2026-10-14 the switch is at the header's top right with an On /
  // Off label left of it (ui/CardHeader.jsx): "Automatic Sign-out ... Off".
  const label = s?.querySelector(".card-head-right [data-state-label]");
  check("the switch says what ON does: sign out automatically",
        sw?.getAttribute("aria-label") === "Sign out automatically when idle"
        && s.querySelector(".card-head-title")?.textContent === "Automatic Sign-out"
        && label?.textContent === "Off" && label.nextElementSibling === sw,
        s?.innerHTML.slice(0, 400));
  check("off: the switch is off and the words say new sign-ins last up to 30 days",
        (sw?.getAttribute("aria-checked") === "false"
         || sw?.getAttribute("data-unchecked") !== null)
        && s.textContent.includes("Automatically sign users out after a period of inactivity.")
        && s.textContent.includes("When disabled, new sign-ins can remain active for up to 30 days."),
        s?.textContent);
  check("...and only new sign-ins are affected",
        s?.textContent.includes("Existing sign-ins keep their current expiration time."));
  check("no wording that contradicts the heading",
        !/Stay signed in|Sessions stay signed in\. Off is not forever/.test(s?.textContent || ""));
  m.act(() => { sw.dispatchEvent(new window.MouseEvent("click", { bubbles: true })); });
  await settle();
  check("turning the switch ON sends auto_logout: true (the stored flag keeps "
        + "its meaning -- no inversion)",
        JSON.stringify(globalThis.__posts.at(-1)) === JSON.stringify(
          ["/api/system/settings", { auto_logout: true }]), globalThis.__posts);
});
await render({ auto_logout: true, idle_minutes: 60, email: true,
               panel_reset: true }, async (box) => {
  const s = section(box);
  check("on: the label left of the switch reads On",
        s?.querySelector(".card-head-right [data-state-label]")?.textContent === "On"
        && !!s.querySelector('[aria-label="Sign out automatically when idle"]'), s?.textContent);
});

const CARDS = /Hailo load balancer|Shared poll cache/i;
const hasSwitchLabel = (box, name) => !!box.querySelector(`[aria-label="${name}"]`);
const emailSwitches = (box) => box.querySelectorAll('[aria-label="Email"]').length;

// email on, non-developer: heading/subtitle, real Email switch, no recovery
// block, no testing cards.
await render({ auto_logout: false, idle_minutes: 43200, email: true,
               panel_reset: true, chip_balance: false, share_polls: false },
  async (box) => {
    const t = box.textContent;
    check("page heading is System with the new subtitle",
          box.querySelector("h1")?.textContent === "System"
          && t.includes("Manage system settings, security, and account access for this device."),
          t.slice(0, 200));
    check("email on: the Email setting and its one working switch stay",
          t.includes("Email") && !t.includes("Email Recovery Unavailable")
          && emailSwitches(box) === 1);
    check("email on: no Password Recovery empty state", !t.includes("Password Recovery"));
    check("non-developer: neither testing card is rendered", !CARDS.test(t), t);
  });

// email off, non-developer: unavailable copy + empty state, one switch only
// (the way back on), cards still hidden.
await render({ auto_logout: false, idle_minutes: 43200, email: false,
               panel_reset: true, chip_balance: true, share_polls: true },
  async (box) => {
    const t = box.textContent;
    check("email off: Email Recovery Unavailable heading and sentence",
          t.includes("Email Recovery Unavailable")
          && t.includes("Email cannot be used to recover accounts or reset passwords on this device."),
          t);
    check("email off: the USER-button sentence",
          t.includes("To reset a forgotten master password, hold the physical USER button on the device for 5 seconds."));
    check("email off: Password Recovery empty state",
          t.includes("Password Recovery")
          && t.includes("This device does not support password recovery by email."));
    check("email off: exactly one Email switch (off, to turn it back on), no second",
          emailSwitches(box) === 1);
    check("non-developer: cards hidden even with both features on", !CARDS.test(t), t);
  });

// the front panel off: the USER-button promise is NOT made.
await render({ auto_logout: false, idle_minutes: 43200, email: false,
               panel_reset: false }, async (box) => {
  check("panel off: no USER-button promise",
        !box.textContent.includes("USER button"), box.textContent);
});

// developer tier: both cards present.
await render({ auto_logout: false, idle_minutes: 43200, email: true,
               panel_reset: true, chip_balance: true, share_polls: true },
  async (box) => {
    const t = box.textContent;
    check("developer: the Hailo load balancer card is shown",
          /Hailo load balancer/.test(t), t);
    check("developer: the shared poll cache card is shown",
          /Shared poll cache/i.test(t), t);
  }, { is_developer: true });

if (bad) {
  console.error(`\n${bad} check(s) failed`);
  process.exit(1);
}
console.log("\nall auto-logout wording checks passed");
dom.window.close();
process.exit(0);
