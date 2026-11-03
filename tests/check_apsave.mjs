// The Network page's Access-point mode card can SAVE a changed name or
// password (Tok Aba, 2026-10-06). Until then a box already broadcasting
// offered only "Turn off AP mode". Pinned, by rendering NetworkPanel against
// a shimmed api (the harness is check_netpreset.mjs's):
//   a. Save is disabled until the name or password differs from the saved one
//      AND is valid; invalid input is explained in a plain sentence (no HTTP
//      code); the password is masked until the eye is pressed
//   b. while broadcasting, Save asks first and says every device drops and
//      must rejoin with the new password, and -- for a browser on the AP --
//      that this page stops responding; No posts nothing
//   c. with AP mode off, Save stores with no confirm
//   d. Reset Wi-Fi names the setup network and the default password iriv1234
//      it brings back (Tok Aba, 2026-10-07), posts none
//   e. (Tok Aba 2026-10-14, the template card) a customer sees the header
//      with an On / Off label left of the switch, "Hotspot is enabled", the
//      SSID field and a BLANK password field ("Enter a new password", the
//      stored key never echoed), the two hints, Save Changes and Reset
//      Settings side by side, and none of the longer text; a name-only save
//      posts no password (the route keeps the stored one)
// a-d run as a developer account (the longer text is a developer's now).
//
//   node tests/check_apsave.mjs
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".apsave-test.cjs");

const dom = new JSDOM("<!doctype html><html><body></body></html>",
                      { pretendToBeVisual: true, url: "https://localhost/" });
global.window = dom.window;
global.document = dom.window.document;
Object.defineProperty(global, "navigator",
                      { value: dom.window.navigator, configurable: true });
for (const k of ["HTMLElement", "HTMLInputElement", "Element", "Node", "SVGElement",
                 "Image", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
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

// The api: GET answers globalThis.__status, every POST is recorded and answers
// the same status (the real routes return the full status on every write).
const shimApi = {
  name: "shim-api",
  setup(b) {
    b.onResolve({ filter: /api\/client\.js$/ }, () => (
      { path: "api-shim", namespace: "shim" }));
    b.onLoad({ filter: /^api-shim$/, namespace: "shim" }, () => ({
      contents: `
        globalThis.__posts = [];
        export const api = {
          post(url, body) { globalThis.__posts.push([url, body]);
                            return Promise.resolve({ ok: true, ...globalThis.__status }); },
          get() { return Promise.resolve(globalThis.__status); },
          put() { return Promise.resolve({}); },
        };
        export function friendlyError(e) { return String(e && e.message || e); }
      `,
      resolveDir: FRONT, loader: "js",
    }));
  },
};
// confirm(): every question recorded, answered from a queue the test fills.
const shimAsk = {
  name: "shim-ask",
  setup(b) {
    b.onResolve({ filter: /ui\/ask\.jsx$/ }, () => (
      { path: "ask-shim", namespace: "shim" }));
    b.onLoad({ filter: /^ask-shim$/, namespace: "shim" }, () => ({
      contents: `
        globalThis.__asked = [];
        globalThis.__answers = [];
        const confirm = async (props) => {
          globalThis.__asked.push(props);
          return globalThis.__answers.length ? globalThis.__answers.shift() : false;
        };
        export function useConfirm() { return confirm; }
        export function useAsk() { return async () => null; }
        export function AskProvider({ children }) { return children; }
      `,
      resolveDir: FRONT, loader: "js",
    }));
  },
};
// The switch as a plain button: what is under test is the page, not the
// component library's switch.
const shimSwitch = {
  name: "shim-switch",
  setup(b) {
    b.onResolve({ filter: /ui\/switch\.jsx$/ }, () => (
      { path: "switch-shim", namespace: "shim" }));
    b.onLoad({ filter: /^switch-shim$/, namespace: "shim" }, () => ({
      contents: `
        import { createElement as h } from "react";
        export function Switch({ checked, onCheckedChange, disabled, ...rest }) {
          return h("button", { type: "button", role: "switch",
            "aria-checked": checked ? "true" : "false", disabled,
            "aria-label": rest["aria-label"],
            onClick: () => onCheckedChange && onCheckedChange(!checked) });
        }
      `,
      resolveDir: FRONT, loader: "js",
    }));
  },
};

const p = (rel) => join(FRONT, rel).replace(/\\/g, "/");
const ENTRY = `
import { createRoot } from "react-dom/client";
import { act, createElement as h } from "react";
import NetworkPanel from "${p("src/components/NetworkPanel.jsx")}";
import { AppContext } from "${p("src/state/app.jsx")}";
export { act, h, NetworkPanel };
// globalThis.__dev: render as a developer account (the longer text shows).
export function mount(el, node) {
  const root = createRoot(el);
  const value = globalThis.__dev ? { user: { is_developer: true } } : null;
  act(() => { root.render(h(AppContext.Provider, { value }, node)); });
  return { unmount: () => act(() => root.unmount()) };
}
`;
await build({
  plugins: [shimApi, shimAsk, shimSwitch],
  stdin: { contents: ENTRY, resolveDir: FRONT, loader: "js" },
  bundle: true, format: "cjs", platform: "node", outfile: OUT,
  jsx: "automatic", logLevel: "silent",
  loader: { ".css": "empty" },
  alias: { "@": join(FRONT, "src") },
  define: { "process.env.NODE_ENV": '"development"' },
});
const m = req(OUT);
const { h, act } = m;

const flush = async () => {
  for (let i = 0; i < 6; i += 1) {
    // eslint-disable-next-line no-await-in-loop
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};
const click = async (el) => {
  await act(async () => { el.dispatchEvent(new window.MouseEvent("click", { bubbles: true })); });
  await flush();
};
const type = async (el, value) => {
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
  await act(async () => {
    setter.call(el, value);
    el.dispatchEvent(new window.Event("input", { bubbles: true }));
  });
};

// A box in access-point mode, the way the tester had it: wlan0 broadcasting,
// eth0 on a cable (the browser is on the AP), eth1 bare, eth2 unmanaged.
const base = {
  ok: true, available: true, radio: true, has_wifi: true, ap: "Hotspot",
  mode: "ap", current_mode: "ap", pending_reboot: false, via: "wlan0",
  ap_name: "iriv-abc", ap_password: "iriv1234", wifi: [], saved: [],
  ipv4_reboot: [],
  devices: [
    { device: "eth0", type: "ethernet", state: "connected", connection: "Wired connection 1",
      ip: "192.168.1.116", prefix: "24", gateway: "192.168.1.1", dns: ["8.8.8.8"],
      method: "auto", dhcp: true },
    { device: "eth1", type: "ethernet", state: "unavailable", connection: "", dhcp: true },
    { device: "eth2", type: "ethernet", state: "unmanaged", connection: "", dhcp: true },
    { device: "wlan0", type: "wifi", state: "connected", connection: "Hotspot",
      ip: "10.42.0.1", prefix: "24", dhcp: true },
  ],
};

async function render(status, fn) {
  globalThis.__status = status;
  globalThis.__posts.length = 0;
  globalThis.__asked.length = 0;
  globalThis.__answers.length = 0;
  const box = document.createElement("div");
  document.body.appendChild(box);
  const r = m.mount(box, h(m.NetworkPanel));
  await flush();
  try { return await fn(box); } finally { r.unmount(); document.body.removeChild(box); }
}

// The card for one interface, by the device it is for (data-net-card).
const card = (box, dev) => box.querySelector(`[data-net-card="${dev}"]`);
const button = (el, text) => [...el.querySelectorAll("button")]
  .find((b) => b.textContent.trim() === text);

// The access-point card: its own panel-card, found by its heading.
globalThis.__dev = true;
const apCard = (box) => [...box.querySelectorAll(".panel-card")]
  .find((c) => c.querySelector("strong")?.textContent === "Device Hotspot");
const nameField = (c) => c.querySelector("[data-ap-name]");
const keyField = (c) => c.querySelector("[data-ap-password]");
const saveBtn = (c) => c.querySelector("[data-ap-save]");
const problem = (c) => c.querySelector("[data-ap-problem]")?.textContent || "";
const savePosts = () => globalThis.__posts.filter(([u]) => u === "/api/hub/network/ap/save");

// -- a. broadcasting, the browser ON the access point ---------------------------------
await render({ ...base, ap_name_factory: "iriv-abc", ap_password: "boxkey2345" }, async (box) => {
  const c = apCard(box);
  check("the card has an editable name, the password and a Save button",
        !!c && !!nameField(c) && !nameField(c).readOnly && !!keyField(c) && !!saveBtn(c));
  check("the password is masked by default", keyField(c).type === "password");
  await click(c.querySelector(".pw-toggle"));
  check("...and the eye shows it", keyField(c).type === "text");
  check("Save is disabled until something changes", saveBtn(c).disabled
        && saveBtn(c).title === "Nothing changed yet");
  check("the card says a save while broadcasting applies straight away",
        c.textContent.includes("Changes to the hotspot name or password take effect immediately"),
        c.textContent);

  await type(keyField(c), "short77");
  await flush();
  check("a 7-character password keeps Save disabled", saveBtn(c).disabled);
  check("...and says why in a plain sentence",
        problem(c) === "The password needs at least 8 characters.", problem(c));
  await type(keyField(c), "cafécaféx");
  await flush();
  check("an accented letter is refused in words", saveBtn(c).disabled
        && problem(c).includes("keyboard symbols only"), problem(c));
  await type(keyField(c), "iriv1234");
  await flush();
  check("the default key iriv1234 is accepted as a new password (Tok Aba, 2026-10-07)",
        !saveBtn(c).disabled && problem(c) === "", problem(c));
  await type(keyField(c), "x".repeat(64));
  await flush();
  check("64 characters is refused", saveBtn(c).disabled && problem(c).includes("at most 63"));
  await type(keyField(c), "newkey6789");
  await flush();
  check("a valid changed password enables Save", !saveBtn(c).disabled && problem(c) === "");
  check("...no HTTP code and no em dash anywhere on the card",
        !/\b(400|403|500)\b/.test(c.textContent) && !c.textContent.includes("\u2014"));

  globalThis.__answers.push(false);
  await click(saveBtn(c));
  const asked = globalThis.__asked[0] || {};
  check("Save while broadcasting asks first", globalThis.__asked.length === 1
        && asked.title === "Restart the hotspot now?", JSON.stringify(asked));
  check("...saying every device is disconnected and must join with the new password",
        (asked.description || "").includes("Every device on it is disconnected and must join "
                                           + "again with the new password"), asked.description);
  check("...and that THIS page drops, since the browser is on the access point",
        (asked.description || "").includes("You are connected through this hotspot, so "
                                           + "this page will stop responding"), asked.description);
  check("No posts nothing", savePosts().length === 0);

  globalThis.__answers.push(true);
  await click(saveBtn(c));
  check("Yes posts the name and password to ap/save",
        savePosts().length === 1 && savePosts()[0][1].ssid === "iriv-abc"
        && savePosts()[0][1].password === "newkey6789", JSON.stringify(globalThis.__posts));
});

// -- b. the name ----------------------------------------------------------------------
await render({ ...base, via: "eth0", ap_name_factory: "iriv-abc", ap_password: "boxkey2345" },
             async (box) => {
  const c = apCard(box);
  await type(nameField(c), "a".repeat(33));
  await flush();
  check("a 33-byte name keeps Save disabled, in words", saveBtn(c).disabled
        && problem(c).startsWith("The network name is too long"), problem(c));
  await type(nameField(c), "é".repeat(17));
  await flush();
  check("...counted in BYTES (17 accented letters are 34)", saveBtn(c).disabled);
  await type(nameField(c), "   ");
  await flush();
  check("an empty name is refused", saveBtn(c).disabled
        && problem(c) === "The network name cannot be empty.");
  await type(nameField(c), "Packing Line");
  await flush();
  check("a new name alone enables Save", !saveBtn(c).disabled);
  globalThis.__answers.push(true);
  await click(saveBtn(c));
  const asked = globalThis.__asked[0] || {};
  check("from a cable the confirm still says devices drop, but not that this page does",
        (asked.description || "").includes("must join again")
        && !(asked.description || "").includes("You are connected through"), asked.description);
  check("...and the post carries the new name",
        savePosts().length === 1 && savePosts()[0][1].ssid === "Packing Line");
});

// -- c. AP mode off: Save only stores --------------------------------------------------
await render({ ...base, ap: "", mode: "wifi", current_mode: "wifi", via: "eth0",
               ap_password: "boxkey2345" }, async (box) => {
  const c = apCard(box);
  check("off, the card says Save stores it for next time",
        c.textContent.includes("Saved changes are used the next time the hotspot is turned on"),
        c.textContent);
  await type(keyField(c), "nextkey4567");
  await flush();
  await click(saveBtn(c));
  check("...and saves with no confirm", globalThis.__asked.length === 0
        && savePosts().length === 1 && savePosts()[0][1].password === "nextkey4567",
        JSON.stringify(globalThis.__posts));
});

// -- d. Reset Hotspot Settings ---------------------------------------------------------
// Customer copy pass (2026-10-11): the default password is no longer printed on
// the page; the card and the confirm point at the device's screen instead. The
// default itself and what the reset posts are unchanged.
await render({ ...base, ap_name: "Packing Line", ap_name_factory: "iriv-abc",
               ap_password: "boxkey2345" }, async (box) => {
  const c = apCard(box);
  check("Reset names the setup network it brings back", c.textContent.includes('"iriv-abc"'));
  check("...says the default password comes back, without printing it",
        c.textContent.includes("default password shown on the device's screen")
        && !c.textContent.includes("iriv1234"), c.textContent);
  await type(keyField(c), "typednotsaved");
  await flush();
  globalThis.__answers.push(true);
  await click(button(c, "Reset Settings"));
  const asked = globalThis.__asked[0] || {};
  check("its confirm says the setup name and the default password return, unprinted",
        (asked.description || "").includes("iriv-abc")
        && (asked.description || "").includes("default password shown on the device's screen")
        && !(asked.description || "").includes("iriv1234")
        && !(asked.description || "").includes("the password is kept")
        && (asked.description || "").includes("Unsaved changes above are discarded"),
        asked.description);
  const reset = globalThis.__posts.filter(([u]) => u === "/api/hub/network/reset");
  check("...and posts NO typed password (the box stores the default itself)",
        reset.length === 1 && !("password" in (reset[0][1] || {})), JSON.stringify(reset));
});

// -- e. the customer's card (Tok Aba 2026-10-14) ---------------------------------------
globalThis.__dev = false;
await render({ ...base, ap_name_factory: "iriv-abc", ap_password: "boxkey2345" }, async (box) => {
  const c = apCard(box);
  const t = c.textContent.replace(/\s+/g, " ");
  const head = c.querySelector(".card-head");
  const pair = head?.querySelector(".card-head-right .state-switch");
  check("e. header: title, subtitle, an On label left of the hotspot switch",
        head?.querySelector(".card-head-sub")?.textContent
          === "Allow phones and computers to connect directly to this device."
        && pair?.querySelector("[data-state-label]")?.textContent === "On"
        && pair.querySelector("[data-state-label]").className === "state-pill is-on"
        && pair.querySelector("[data-state-label]").nextElementSibling
          === c.querySelector('[aria-label="Device hotspot"]'));
  check("...Hotspot is enabled, with its line",
        c.querySelector("[data-ap-on]")?.textContent.replace(/\s+/g, " ").includes(
          "Hotspot is enabledNearby devices can connect using the network name and password below."));
  check("...Network name (SSID) with the name and its hint",
        t.includes("Network name (SSID)") && nameField(c).value === "iriv-abc"
        && t.includes("The Wi-Fi name shown when connecting to this device."));
  check("...Hotspot password BLANK with the placeholder, never the stored key, an eye, the hint",
        t.includes("Hotspot password") && keyField(c).value === ""
        && keyField(c).getAttribute("placeholder") === "Enter a new password"
        && !c.innerHTML.includes("boxkey2345") && !!c.querySelector(".pw-toggle")
        && t.includes("At least 8 characters."));
  const save = button(c, "Save Changes");
  const reset = button(c, "Reset Settings");
  check("...Save Changes (disabled, nothing changed) beside a red Reset Settings",
        save?.disabled && save.classList.contains("btn-accent")
        && reset?.classList.contains("btn-danger") && save.parentElement === reset.parentElement);
  check("...and none of the longer text",
        !c.querySelector("[data-ap-dev-extras]") && !t.includes("Switching happens straight away")
        && !t.includes("take effect immediately") && !t.includes("Restore the default"), t);
  await type(nameField(c), "Line 2");
  await flush();
  globalThis.__answers.push(true);
  await click(save);
  const asked = globalThis.__asked[0] || {};
  check("a name-only save asks, naming the current password, and posts no password",
        (asked.description || "").includes("with its current password")
        && savePosts().length === 1 && savePosts()[0][1].ssid === "Line 2"
        && !("password" in savePosts()[0][1]), JSON.stringify(globalThis.__posts));
});
await render({ ...base, ap: "", mode: "wifi", current_mode: "wifi", via: "eth0" }, async (box) => {
  const c = apCard(box);
  check("off: the label says Off (plain), no Hotspot is enabled",
        c.querySelector(".card-head [data-state-label]")?.textContent === "Off"
        && c.querySelector(".card-head [data-state-label]").className === "state-pill"
        && !c.querySelector("[data-ap-on]"));
  await type(keyField(c), "short77");
  await flush();
  check("...a short password is refused and the switch will not turn on",
        problem(c) === "The password needs at least 8 characters."
        && c.querySelector('[aria-label="Device hotspot"]').disabled);
});

if (bad) {
  console.error(`\n${bad} check(s) failed`);
  process.exit(1);
}
console.log("\nall access point save checks passed");
dom.window.close();
process.exit(0);
