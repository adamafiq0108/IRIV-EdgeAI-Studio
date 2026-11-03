// Settings > MQTT Broker: the customer copy pass (2026-10-11).
//
// Wording and hierarchy only; ports (1883 default), login, who-can-connect,
// the root helper, auto-install and persistence are unchanged. Pinned here:
// the spec headings and help; the auto-install switch, the persistence note
// and the TLS status shown to the developer tier only (no dead TLS checkbox
// for anybody); Apply saying it restarts the broker and drops clients; Reset
// asking "Discard Unsaved Changes?" only when something changed (it re-reads
// the saved settings, it does not restore defaults); the validation titles,
// which refuse only what mqtt_broker.build_request refuses; and no raw
// server or helper text in an Apply failure.
//
//   node tests/check_brokercopy.mjs
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".brokercopy-test.cjs");

const dom = new JSDOM("<!doctype html><html><body></body></html>",
                      { pretendToBeVisual: true, url: "https://localhost/" });
global.window = dom.window;
global.document = dom.window.document;
Object.defineProperty(global, "navigator",
                      { value: dom.window.navigator, configurable: true });
for (const k of ["HTMLElement", "Element", "Node", "SVGElement", "Image",
                 "Event", "CustomEvent", "MouseEvent", "HTMLInputElement",
                 "requestAnimationFrame", "cancelAnimationFrame",
                 "getComputedStyle", "localStorage"]) {
  global[k] = dom.window[k];
}
global.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
global.IS_REACT_ACT_ENVIRONMENT = true;

let bad = 0;
const check = (msg, passed, detail = "") => {
  if (passed) console.log(`ok   ${msg}`);
  else { console.error(`FAIL ${msg}${detail ? `\n      ${detail}` : ""}`); bad += 1; }
};

// POST answers per url from globalThis.__answer: a value, or an Error with a
// status to reject with.
const shimApi = {
  name: "shim-api",
  setup(b) {
    b.onResolve({ filter: /api\/client\.js$/ }, () => (
      { path: "api-shim", namespace: "shim" }));
    b.onLoad({ filter: /^api-shim$/, namespace: "shim" }, () => ({
      contents: `
        globalThis.__posts = [];
        export const api = {
          get() { return Promise.resolve(JSON.parse(JSON.stringify(globalThis.__broker))); },
          post(url, body) {
            globalThis.__posts.push([url, body]);
            const a = (globalThis.__answer || {})[url];
            if (a && a.status) {
              const e = new Error(a.message); e.status = a.status;
              return Promise.reject(e);
            }
            return Promise.resolve(a || JSON.parse(JSON.stringify(globalThis.__broker)));
          },
        };
        export function friendlyError(e) { return "friendly:" + (e && e.status); }
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
      contents: `export function useApp() { return { user: globalThis.__user }; }`,
      resolveDir: FRONT, loader: "js",
    }));
  },
};

const p = (rel) => join(FRONT, rel).replace(/\\/g, "/");
const ENTRY = `
import { createRoot } from "react-dom/client";
import { act, createElement as h } from "react";
import BrokerPanel from "${p("src/components/BrokerPanel.jsx")}";
import { ToastProvider } from "${p("src/components/ui/Toast.jsx")}";
import { AskProvider } from "${p("src/components/ui/ask.jsx")}";
export { act, h, BrokerPanel, ToastProvider, AskProvider };
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
  loader: { ".css": "empty" },
  define: { "process.env.NODE_ENV": '"development"' },
});
const m = req(OUT);
const { h } = m;
const settle = () => m.act(async () => { await new Promise((r) => setTimeout(r, 20)); });
const click = (el) => m.act(async () => {
  el.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
});
const button = (root, label) => [...root.querySelectorAll("button")]
  .find((b) => b.textContent.trim() === label);
const field = (box, label) => [...box.querySelectorAll("label")]
  .find((l) => l.querySelector("span")?.textContent.trim().startsWith(label));
async function type(input, value) {
  const set = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype, "value").set;
  await m.act(async () => {
    set.call(input, value);
    input.dispatchEvent(new window.Event("input", { bubbles: true }));
  });
}
const toastText = () => document.body.textContent;

async function render(broker, fn, user = { is_developer: false }) {
  globalThis.__broker = broker;
  globalThis.__user = user;
  globalThis.__posts = [];
  globalThis.__answer = {};
  const box = document.createElement("div");
  document.body.appendChild(box);
  const r = m.mount(box, h(m.ToastProvider, null,
                           h(m.AskProvider, null, h(m.BrokerPanel))));
  await settle();
  try { return await fn(box); } finally { r.unmount(); document.body.removeChild(box); }
}

const BROKER = (over = {}) => ({
  status: { state: "running", port: 1883, mode: "open", helper: true,
            managed: true, access: "network", ws_port: null, ...over },
  settings: { username: "", password_set: false },
  defaults: { port: 1883 }, setup_commands: [], auto_install: true,
  addresses: ["192.168.1.20"],
  persistence_note: "Retained messages and sessions already survive a restart: the distribution's mosquitto.conf turns persistence on.",
});
const flat = (el) => (el?.textContent || "").replace(/\s+/g, " ");
const sectionTitles = (box) => [...box.querySelectorAll("h2")].map((x) => x.textContent.trim());

await render(BROKER(), async (box) => {
  const t = flat(box);
  check("spec heading and subtitle",
        box.querySelector("h1")?.textContent === "MQTT Broker"
        && t.includes("Configure how other devices and applications connect to this device to exchange messages."));
  check("sections: Broker Status, Login Credentials, Connection Settings, Who Can Connect",
        JSON.stringify(sectionTitles(box)) === JSON.stringify(
          ["Broker Status", "Login Credentials", "Connection Settings", "Who Can Connect"]),
        sectionTitles(box));
  check("running: MQTT Broker Running, the sentence, the port",
        t.includes("MQTT Broker Running")
        && flat(box.querySelector("[data-broker-running]")).includes(
          "The MQTT broker is running and ready to accept connections using the configured network settings. Port 1883"));
  check("Connection Addresses with a Copy button per address",
        flat(box.querySelector("[data-broker-reachable]")).includes("Connection Addresses")
        && box.querySelector('[aria-label="Copy 192.168.1.20:1883"]')?.title === "Copy Connection Address");
  check("customer: no auto-install switch, no persistence note, no TLS",
        !box.querySelector("[data-broker-auto]") && !t.includes("Retained messages")
        && !/TLS\)|SSL\/TLS|coming later/.test(t) && !box.querySelector("[data-broker-tls]"));
  check("the spec labels",
        t.includes("Require a username and password to connect") && t.includes("MQTT Port")
        && t.includes("WebSocket Port (Optional)")
        && box.querySelector('input[placeholder="Leave blank to disable"]')?.value === ""
        && t.includes("This Device Only") && t.includes("Devices on the Network"));
  check("the port keeps 1883", box.querySelector('input[placeholder="1883"]')?.value === "1883");
  check("network + no login: both warnings, saying there are no topic limits",
        flat(box.querySelector('[data-broker-note="auth-off"]')).includes("Authentication Disabled")
        && flat(box.querySelector('[data-broker-note="open"]')).includes("Warning: Unprotected MQTT Access")
        && flat(box.querySelector('[data-broker-note="open"]')).includes("read every message and publish to any topic"));
  check("Apply says the broker restarts and clients drop",
        flat(box.querySelector("[data-broker-apply-help]")).includes(
          "The broker restarts, so connected MQTT clients are disconnected and must reconnect."));

  // Validation before posting.
  const portIn = box.querySelector('input[placeholder="1883"]');
  await type(portIn, "70000");
  await click(button(box, "Apply")); await settle();
  check("bad port: Invalid Port, nothing posted",
        toastText().includes("Invalid Port") && globalThis.__posts.length === 0);
  // Reset with a change asks first; Cancel keeps the edit.
  await click(button(box, "Reset")); await settle();
  check("Reset with unsaved changes asks Discard Unsaved Changes?",
        document.body.textContent.includes("Discard Unsaved Changes?")
        && !!button(document.body, "Discard Changes"));
  await click(button(document.body, "Discard Changes")); await settle();
  check("...and Discard Changes re-reads the saved settings",
        box.querySelector('input[placeholder="1883"]')?.value === "1883");
  await click(box.querySelector('input[type="checkbox"]')); // require login
  await click(button(box, "Apply")); await settle();
  check("login on, no username: Username Required",
        toastText().includes("Username Required") && globalThis.__posts.length === 0);
  await type(box.querySelector('[data-broker-field="username"]'), "plant");
  await click(button(box, "Apply")); await settle();
  check("login on, no saved password: Password Required",
        toastText().includes("Password Required") && globalThis.__posts.length === 0);
  await type(box.querySelector('[data-broker-field="password"]'), "longenough");
  globalThis.__answer["/api/mqtt/broker"] = { status: 400,
    message: "broker did not come up: Oct 11 mosquitto[123]: Error: secret" };
  await click(button(box, "Apply")); await settle();
  check("an Apply failure: Could Not Apply Settings, never the helper's text",
        globalThis.__posts.length === 1
        && toastText().includes("Could Not Apply Settings")
        && toastText().includes("The MQTT broker settings could not be applied.")
        && !toastText().includes("mosquitto[123]"));
});

await render(BROKER(), async (box) => {
  check("Reset with nothing changed just re-reads, no question",
        (await click(button(box, "Reset")), await settle(),
         !document.body.textContent.includes("Discard Unsaved Changes?")));
});

await render(BROKER(), async (box) => {
  const t = flat(box);
  check("developer: the auto-install switch is there and still works",
        !!box.querySelector("[data-broker-auto]"));
  await click(box.querySelector("[data-broker-auto]")); await settle();
  check("...posting the same route as before",
        globalThis.__posts.some(([u, b]) => u === "/api/mqtt/broker/auto" && b.enabled === false));
  check("developer: the persistence note and the TLS status",
        t.includes("Retained messages and sessions already survive a restart")
        && flat(box.querySelector("[data-broker-tls]")).includes("Secure MQTT Connection (TLS)")
        && flat(box.querySelector("[data-broker-tls]")).includes("Not available yet")
        && !box.querySelector("[data-broker-tls] input"));
}, { is_developer: true });

if (bad) {
  console.error(`\n${bad} check(s) failed`);
  process.exit(1);
}
console.log("\nall MQTT broker copy checks passed");
dom.window.close();
process.exit(0);
