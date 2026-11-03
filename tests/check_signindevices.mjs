// Accounts > Login Sessions as REGISTERED DEVICES, and the login refusal.
//
// The screenshot that started it: one account, eleven rows of "Chrome -
// Windows - 192.168.1.x", each its own Sign Out. The same few browsers had
// signed in again and again. Now a browser is a device (core/services/
// auth.py), its sessions fold under it, and an account may register ten.
// Mounted in JSDOM against a shimmed api, this pins:
//
//   * one row per device, not per session, with name, Online/Offline, IP,
//     MAC or "-" with the reason, last active (exact time on hover);
//   * "This device", the counter "Registered devices: N of 10", the 30-day
//     line, and master's "no limit";
//   * Remove Device and Remove Other Devices each ask first, say the device
//     must sign in and register again, and call the right route only on yes;
//   * Sign Out per session still there (behind Show Sessions), and Sign Out
//     Other Sessions still there;
//   * an admin sees other accounts grouped, each with its own counter;
//   * the login card shows the server's device-limit sentence.
//
//   node tests/check_signindevices.mjs
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".signindevices-test.cjs");

const dom = new JSDOM("<!doctype html><html><body></body></html>",
                      { pretendToBeVisual: true, url: "http://localhost/" });
global.window = dom.window;
global.document = dom.window.document;
Object.defineProperty(global, "navigator",
                      { value: dom.window.navigator, configurable: true });
for (const k of ["HTMLElement", "Element", "Node", "SVGElement", "Image",
                 "Event", "CustomEvent", "MouseEvent", "FormData",
                 "requestAnimationFrame", "cancelAnimationFrame",
                 "getComputedStyle", "localStorage", "sessionStorage"]) {
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

const shimApi = {
  name: "shim-api",
  setup(b) {
    b.onResolve({ filter: /api\/client\.js$/ }, () => (
      { path: "api-shim", namespace: "shim" }));
    b.onLoad({ filter: /^api-shim$/, namespace: "shim" }, () => ({
      contents: `
        globalThis.__calls = [];
        export class ApiError extends Error {
          constructor(m, s, d) { super(m); this.status = s; this.data = d; } }
        export const api = {
          get(url) { globalThis.__calls.push(["GET", url]);
                     return Promise.resolve(globalThis.__sessions); },
          post(url, body) { globalThis.__calls.push(["POST", url, body]);
                            if (globalThis.__postFail) return Promise.reject(globalThis.__postFail);
                            return Promise.resolve({ ok: true, removed: 2, revoked: 3 }); },
          del(url) { globalThis.__calls.push(["DELETE", url]);
                     return Promise.resolve({ ok: true }); },
          put() { return Promise.resolve({}); },
        };
        export function friendlyError(e) { return "friendly: " + String(e && e.message || e); }
        export function saveBlob() {}
      `,
      resolveDir: FRONT, loader: "js",
    }));
  },
};
const shimApp = {
  name: "shim-app",
  setup(b) {
    b.onResolve({ filter: /state\/app\.jsx$/ }, () => (
      { path: "app-shim", namespace: "shim" }));
    b.onLoad({ filter: /^app-shim$/, namespace: "shim" }, () => ({
      contents: `export function useApp() { return { user: { name: "alice" } }; }
                 export function applyBrand() {}`,
      resolveDir: FRONT, loader: "js",
    }));
  },
};

const p = (rel) => join(FRONT, rel).replace(/\\/g, "/");
const ENTRY = `
import { createRoot } from "react-dom/client";
import { act, createElement as h } from "react";
import { MemoryRouter } from "react-router-dom";
import { SessionsPane } from "${p("src/components/AccountsPanel.jsx")}";
import Login from "${p("src/pages/Login.jsx")}";
import { ToastProvider } from "${p("src/components/ui/Toast.jsx")}";
export { act, h, SessionsPane, Login, ToastProvider, MemoryRouter };
export function mount(el, node) {
  const root = createRoot(el);
  act(() => { root.render(node); });
  return { unmount: () => act(() => root.unmount()) };
}
`;
await build({
  plugins: [shimApi, shimApp],
  stdin: { contents: ENTRY, resolveDir: FRONT, loader: "js" },
  bundle: true, format: "cjs", platform: "node", outfile: OUT,
  jsx: "automatic", logLevel: "silent",
  alias: { "@": join(FRONT, "src") },
  loader: { ".css": "empty", ".png": "empty", ".svg": "empty" },
  define: { "process.env.NODE_ENV": '"development"' },
});
const m = req(OUT);
const { h } = m;
const settle = () => m.act(async () => { await new Promise((r) => setTimeout(r, 20)); });
const click = (el) => m.act(async () => {
  el.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 20));
});

const now = Math.floor(Date.now() / 1000);
const sess = (sid, current = false, seen = now - 30) => (
  { sid, login: now - 3600, seen, ip: "192.168.1.20", current });
const DEV = (over) => ({
  id: "d000000000001", user: "alice", name: "Chrome · Windows", ip: "192.168.1.20",
  mac: "aa:bb:cc:dd:ee:01", status: "online", online: true, seen: now - 30,
  first: now - 86400, registered: true, legacy: false, current: false,
  sessions_count: 1, sessions: [sess("s1")], ...over });

function payload(over = {}) {
  const devices = over.devices || [
    DEV({ id: "d000000000001", current: true, sessions_count: 3,
          sessions: [sess("s1", true), sess("s2"), sess("s3")] }),
    DEV({ id: "d000000000002", name: "Safari · iPhone", ip: "10.0.0.7", mac: "",
          online: false, status: "offline", seen: now - 3 * 86400,
          sessions: [sess("s4", false, now - 3 * 86400)] }),
  ];
  const counts = {};
  for (const d of devices) if (!d.legacy) counts[d.user] = (counts[d.user] || 0) + 1;
  return {
    ok: true, admin: false, auto_logout: false, idle_minutes: 43200,
    sessions: devices.flatMap((d) => d.sessions.map((s) => ({ ...s, user: d.user,
                                                               device: d.name }))),
    devices, device_counts: counts, device_limit: 10, device_capped: true,
    device_count: counts.alice || 0, device_expiry_days: 30, device_online_minutes: 5,
    unlisted: false, ...over,
  };
}

async function render(data, fn, answers = []) {
  globalThis.__sessions = data;
  globalThis.__calls = [];
  globalThis.__postFail = null;
  const asked = [];
  const confirm = async (opts) => { asked.push(opts); return answers.length ? answers.shift() : true; };
  const box = document.createElement("div");
  document.body.appendChild(box);
  const r = m.mount(box, h(m.ToastProvider, null,
                           h(m.SessionsPane, { me: "alice", confirm })));
  await settle();
  try { return await fn(box, asked); } finally { r.unmount(); document.body.removeChild(box); }
}
const buttons = (box, text) => [...box.querySelectorAll("button")]
  .filter((b) => b.textContent.trim().startsWith(text));

// -- the list ----------------------------------------------------------------------
await render(payload(), async (box, asked) => {
  const rows = box.querySelectorAll("li.acct-device");
  const text = box.textContent;
  check("two devices are two rows (three sessions of one browser are one row)",
        rows.length === 2, rows.length);
  check("the counter reads Registered devices: 2 of 10",
        text.includes("Registered devices: 2 of 10"), text.slice(0, 400));
  check("the 30-day rule is said in the owner's words",
        text.includes("A device that has not been active for 30 days is removed automatically."));
  check("each row names the device (browser and system)",
        rows[0].textContent.includes("Chrome · Windows")
        && rows[1].textContent.includes("Safari · iPhone"));
  check("...says Online or Offline beside a status dot",
        rows[0].textContent.includes("Online") && rows[1].textContent.includes("Offline")
        && rows[0].querySelector(".acct-status-dot.is-online")
        && rows[0].querySelector(".acct-status-dot").getAttribute("aria-hidden") === "true");
  check("...shows the IP and the MAC when the box can see one",
        rows[0].textContent.includes("IP 192.168.1.20")
        && rows[0].textContent.includes("aa:bb:cc:dd:ee:01"));
  const dash = [...rows[1].querySelectorAll("span[title]")]
    .find((s) => s.title.startsWith("Not visible"));
  check("...and '-' with the reason when it cannot (title and screen-reader text)",
        dash && dash.textContent.startsWith("-")
        && dash.title === "Not visible from another network or a phone with a private Wi-Fi address"
        && dash.querySelector(".sr-only"), rows[1].innerHTML.slice(0, 600));
  const t = rows[1].querySelector("time");
  check("last active is relative, exact on hover",
        /Last active 3 d ago/.test(t?.textContent || "") && t.title.length > 5
        && t.getAttribute("datetime"), t?.outerHTML);
  check("the browser in use carries This device and no Remove Device",
        rows[0].textContent.includes("This device")
        && !buttons(rows[0], "Remove Device").length);
  check("the other device has Remove Device, with an accessible name",
        buttons(rows[1], "Remove Device").length === 1
        && /Remove device Safari · iPhone of alice/.test(
          buttons(rows[1], "Remove Device")[0].getAttribute("aria-label")));
  check("no device id or session id is printed",
        !text.includes("d000000000001") && !/\bs[1-4]\b/.test(text));
  check("Sign Out Other Sessions and Remove Other Devices are both offered",
        buttons(box, "Sign Out Other Sessions").length === 1
        && buttons(box, "Remove Other Devices").length === 1);

  // Sessions behind a disclosure, each with Sign Out (the current one labelled).
  const toggle = buttons(rows[0], "Show Sessions")[0];
  check("a device's sessions sit behind Show Sessions (3), collapsed",
        toggle && toggle.textContent.includes("(3)")
        && toggle.getAttribute("aria-expanded") === "false");
  await click(toggle);
  await settle();
  const sessRows = rows[0].querySelectorAll("li.acct-session");
  check("...opened, each session is listed with Sign Out, the current one labelled",
        sessRows.length === 3 && toggle.getAttribute("aria-expanded") === "true"
        && sessRows[0].textContent.includes("Current Session")
        && buttons(rows[0], "Sign Out").length === 2);
  await click(buttons(rows[0], "Sign Out")[0]);
  await settle();
  check("Sign Out asks first and says the device stays registered",
        asked.at(-1)?.title === "Sign out this session?"
        && /device stays registered/.test(asked.at(-1)?.description));
  check("...and ends that one session",
        globalThis.__calls.some(([v, u]) => v === "DELETE" && u === "/api/auth/sessions/s2"));
});

// -- Remove Device: asked, explained, only on yes -------------------------------------
await render(payload(), async (box, asked) => {
  const row = box.querySelectorAll("li.acct-device")[1];
  await click(buttons(row, "Remove Device")[0]);
  await settle();
  const q = asked.at(-1);
  check("Remove Device asks Remove this device?",
        q?.title === "Remove this device?" && q?.confirmLabel === "Remove Device"
        && q?.destructive === true, JSON.stringify(q));
  check("...and says it must sign in and register again, against the limit",
        /must sign in and register again/.test(q?.description)
        && /limit of 10 devices/.test(q?.description), q?.description);
  check("...a No sends nothing",
        !globalThis.__calls.some(([v]) => v === "DELETE"), JSON.stringify(globalThis.__calls));
}, [false]);
await render(payload(), async (box) => {
  const row = box.querySelectorAll("li.acct-device")[1];
  await click(buttons(row, "Remove Device")[0]);
  await settle();
  check("...a Yes removes exactly that device",
        globalThis.__calls.some(([v, u]) => v === "DELETE"
                                && u === "/api/auth/devices/d000000000002"));
});

// -- Remove Other Devices ----------------------------------------------------------------
await render(payload(), async (box, asked) => {
  await click(buttons(box, "Remove Other Devices")[0]);
  await settle();
  const q = asked.at(-1);
  check("Remove Other Devices asks first, destructive, and explains re-registration",
        q?.title === "Remove other devices?" && q?.destructive === true
        && /register again/.test(q?.description) && /This device stays/.test(q?.description));
  check("...then posts remove-others",
        globalThis.__calls.some(([v, u]) => v === "POST"
                                && u === "/api/auth/devices/remove-others"));
});
await render(payload(), async (box, asked) => {
  await click(buttons(box, "Sign Out Other Sessions")[0]);
  await settle();
  check("Sign Out Other Sessions still works and says registrations stay",
        asked.at(-1)?.title === "Sign out other sessions?"
        && /stays registered/.test(asked.at(-1)?.description)
        && globalThis.__calls.some(([v, u]) => v === "POST"
                                   && u === "/api/auth/sessions/revoke-others"));
});

// -- a device of only this browser: no bulk buttons ----------------------------------------
await render(payload({ devices: [DEV({ current: true, sessions: [sess("s1", true)] })] }),
  async (box) => {
    check("with only this device, neither bulk button is shown",
          !buttons(box, "Remove Other Devices").length
          && !buttons(box, "Sign Out Other Sessions").length);
    check("...and the counter reads 1 of 10", box.textContent.includes("Registered devices: 1 of 10"));
  });

// -- legacy, admin, master ----------------------------------------------------------------
await render(payload({
  admin: true, device_capped: false,
  devices: [
    DEV({ current: true, sessions: [sess("s1", true)] }),
    DEV({ id: "legacy-0123456789ab", legacy: true, registered: false,
          name: "Firefox · Linux", sessions: [sess("s9")] }),
    DEV({ id: "d000000000003", user: "bob", name: "Edge · Windows" }),
  ],
}), async (box) => {
  const text = box.textContent;
  check("a pre-update sign-in is marked Not registered yet and not counted",
        text.includes("Not registered yet")
        && text.includes("alice - Registered devices: 1 (no limit for this account)"),
        text.slice(0, 600));
  check("an admin sees the other accounts grouped, each with its own counter",
        text.includes("bob - Registered devices: 1 of 10")
        && box.querySelectorAll("section[aria-label^='Registered devices of']").length === 2);
});

// -- the login card shows the server's sentence ------------------------------------------
const LIMIT = "This account already has 10 registered devices. Remove one in "
  + "Accounts > Login Sessions, or ask an administrator.";
async function loginWith(err) {
  globalThis.__sessions = { brand: {} };
  globalThis.__postFail = err;
  const box = document.createElement("div");
  document.body.appendChild(box);
  const r = m.mount(box, h(m.MemoryRouter, null, h(m.Login)));
  await settle();
  box.querySelector("input[name=username]").value = "alice";
  box.querySelector("input[name=password]").value = "alice-pass-1";
  await m.act(async () => {
    box.querySelector("form").dispatchEvent(
      new window.Event("submit", { bubbles: true, cancelable: true }));
    await new Promise((res) => setTimeout(res, 20));
  });
  const msg = box.querySelector(".login-error");
  const out = { text: msg?.textContent || "", role: msg?.getAttribute("role") };
  r.unmount(); document.body.removeChild(box);
  return out;
}
const e403 = Object.assign(new Error(LIMIT), { status: 403,
                                               data: { device_limit: true, error: LIMIT } });
const got = await loginWith(e403);
check("the login card shows the device-limit sentence word for word, as an alert",
      got.text === LIMIT && got.role === "alert", JSON.stringify(got));
const other = await loginWith(Object.assign(new Error("nope"), { status: 403, data: {} }));
check("...while any other 403 still reads as the friendly error",
      other.text.startsWith("friendly:"), other.text);
const wrong = await loginWith(Object.assign(new Error("x"), { status: 401, data: {} }));
check("...and a 401 is still Wrong username or password.",
      wrong.text === "Wrong username or password.");

console.log(bad ? "FAILED" : "PASS");
process.exit(bad ? 1 : 0);
