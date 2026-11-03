// The Network page switches wlan0 between access point and Wi-Fi NOW, with no
// reboot (Tok Aba, 2026-10-07; core/services/wifiswitch.py). Pinned by
// rendering NetworkPanel against a shimmed api (check_apsave.mjs's harness):
//   a. the Access-point mode card says the switch happens now and that the
//      hotspot NEVER turns on by itself (Tok Aba, 2026-10-14: the 45 s
//      rollback is gone); nothing on the mode path talks about a reboot
//   b. AP -> Wi-Fi from a browser ON the access point: a dialog picks the
//      network, then a confirm says this page stops, where the box will be
//      (name.local, the address on its screen) and that the hotspot will
//      NOT come back by itself (a cable, or the right password);
//      No posts nothing, Yes posts {on:false, network} and never a reboot
//   c. no saved network: the dialog asks for a name and password instead
//   d. Wi-Fi -> AP: from a cable the confirm says no reboot and Ethernet is
//      unaffected; from wlan0 it says which network to join and the AP's
//      address; Yes posts {on:true}
//   e. a switch under way shows on the page and locks the controls; a failed
//      one shows its reason, says the hotspot is off and offers Turn Hotspot
//      On (which asks first); the page follows the switch to its outcome
//   f. from wlan0 the switch covers the whole window (keys swallowed), says
//      which network to join, and opens the box only at the candidate that
//      answers GET /api/health/box as THIS box with its switch over
//   g. (Pipi M1) the session travels only when this computer is NOT changing
//      networks and the address is the box's own; never off wlan0
//   h. (Tok Aba 2026-10-08) Search Wi-Fi lists networks with the AP up; a
//      picked secure network needs its password; Test connection confirms,
//      posts network/wifi/test, covers the page while the AP is off and
//      shows "Wi-Fi connection is ready" or the reason in the dialog, with
//      what was typed kept; Test and switch is the switch, and a failure
//      reopens the dialog filled in with the reason; an older helper on
//      the box offers "Switch without testing" only
//
//   node tests/check_wifiswitch.mjs
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".wifiswitch-test.cjs");

const dom = new JSDOM("<!doctype html><html><body></body></html>",
                      { pretendToBeVisual: true, url: "https://localhost/" });
global.window = dom.window;
global.document = dom.window.document;
Object.defineProperty(global, "navigator",
                      { value: dom.window.navigator, configurable: true });
for (const k of ["HTMLElement", "HTMLInputElement", "HTMLSelectElement", "Element", "Node",
                 "SVGElement", "Image", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
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

const shim = (name, filter, contents) => ({
  name,
  setup(b) {
    b.onResolve({ filter }, () => ({ path: name, namespace: "shim" }));
    b.onLoad({ filter: new RegExp(`^${name}$`), namespace: "shim" }, () => (
      { contents, resolveDir: FRONT, loader: "js" }));
  },
});
// GET answers globalThis.__status; every POST is recorded and answers
// globalThis.__reply (else the status), the way the routes answer the status.
const shimApi = shim("api-shim", /api\/client\.js$/, `
  globalThis.__posts = [];
  export const api = {
    post(url, body) { globalThis.__posts.push([url, body]);
                      return Promise.resolve({ ok: true, ...(globalThis.__reply || globalThis.__status) }); },
    get() { return globalThis.__getFail ? Promise.reject(new Error("unreachable"))
                                        : Promise.resolve(globalThis.__status); },
    put() { return Promise.resolve({}); },
  };
  export function friendlyError(e) { return String(e && e.message || e); }
`);
const shimAsk = shim("ask-shim", /ui\/ask\.jsx$/, `
  globalThis.__asked = [];
  globalThis.__answers = [];
  const confirm = async (props) => {
    globalThis.__asked.push(props);
    return globalThis.__answers.length ? globalThis.__answers.shift() : false;
  };
  export function useConfirm() { return confirm; }
  export function useAsk() { return async () => null; }
  export function AskProvider({ children }) { return children; }
`);
const shimToast = shim("toast-shim", /ui\/Toast\.jsx$/, `
  globalThis.__toasts = [];
  const t = {};
  for (const k of ["info", "success", "warn", "error"]) {
    t[k] = (title, detail) => globalThis.__toasts.push([k, title, detail || ""]);
  }
  t.dismiss = () => {}; t.run = (p) => p; t.undoable = (_m, c) => c && c();
  export function useToast() { return t; }
  export function ToastProvider({ children }) { return children; }
`);
const shimSwitch = shim("switch-shim", /ui\/switch\.jsx$/, `
  import { createElement as h } from "react";
  export function Switch({ checked, onCheckedChange, disabled, ...rest }) {
    return h("button", { type: "button", role: "switch",
      "aria-checked": checked ? "true" : "false", disabled,
      "aria-label": rest["aria-label"],
      onClick: () => onCheckedChange && onCheckedChange(!checked) });
  }
`);

const p = (rel) => join(FRONT, rel).replace(/\\/g, "/");
const ENTRY = `
import { createRoot } from "react-dom/client";
import { act, createElement as h } from "react";
import NetworkPanel from "${p("src/components/NetworkPanel.jsx")}";
import { AppContext } from "${p("src/state/app.jsx")}";
import { openUrl } from "${p("src/components/WifiSwitchOverlay.jsx")}";
import { sessionCarriesTo } from "${p("src/lib/session.js")}";
export { act, h, NetworkPanel, openUrl, sessionCarriesTo };
// globalThis.__dev: render as a developer account (the longer text shows).
export function mount(el, node) {
  const root = createRoot(el);
  const value = globalThis.__dev ? { user: { is_developer: true } } : null;
  act(() => { root.render(h(AppContext.Provider, { value }, node)); });
  return { unmount: () => act(() => root.unmount()) };
}
`;
await build({
  plugins: [shimApi, shimAsk, shimToast, shimSwitch],
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
  await flush();
};
const choose = async (el, value) => {
  const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, "value").set;
  await act(async () => {
    setter.call(el, value);
    el.dispatchEvent(new window.Event("change", { bubbles: true }));
  });
  await flush();
};

// A box in access-point mode, the browser on the AP, eth0 on a cable.
const base = {
  ok: true, available: true, radio: true, has_wifi: true, ap: "Hotspot",
  mode: "ap", current_mode: "ap", mode_mismatch: false, via: "wlan0",
  ap_name: "iriv-abc", ap_name_factory: "iriv-abc", ap_password: "iriv1234",
  wifi: [], saved: [{ name: "Plant", active: false }, { name: "Office", active: false }],
  ipv4_reboot: [], box_url: "iriv-abc.local:5100",
  switch: { state: "idle", ap_address: "10.42.0.1", budget_s: 45 },
  devices: [
    { device: "eth0", type: "ethernet", state: "connected", connection: "Wired connection 1",
      ip: "192.168.1.116", prefix: "24", gateway: "192.168.1.1", dns: ["8.8.8.8"],
      method: "auto", dhcp: true },
    { device: "wlan0", type: "wifi", state: "connected", connection: "Hotspot",
      ip: "10.42.0.1", prefix: "24", dhcp: true },
  ],
};
const onWifi = {
  ...base, ap: "", mode: "wifi", current_mode: "wifi", via: "eth0",
  devices: [base.devices[0],
            { device: "wlan0", type: "wifi", state: "connected", connection: "Plant",
              ip: "192.168.1.77", prefix: "24", dhcp: true }],
};

async function render(status, fn) {
  globalThis.__status = status;
  globalThis.__reply = null;
  globalThis.__posts.length = 0;
  globalThis.__asked.length = 0;
  globalThis.__answers.length = 0;
  globalThis.__toasts.length = 0;
  const box = document.createElement("div");
  document.body.appendChild(box);
  const r = m.mount(box, h(m.NetworkPanel));
  await flush();
  try { return await fn(box); } finally { r.unmount(); document.body.removeChild(box); }
}

// The cases below read the hotspot card's longer text, which is a
// developer account's since 2026-10-14; the On / Off switch at the card's
// top right replaced the Turn On / Turn Off Hotspot buttons (same handlers).
globalThis.__dev = true;
const apSwitch = (c) => c.querySelector('[aria-label="Device hotspot"]');
const apCard = (box) => [...box.querySelectorAll(".panel-card")]
  .find((c) => c.querySelector("strong")?.textContent === "Device Hotspot");
const wifiCard = (box) => [...box.querySelectorAll(".panel-card")]
  .find((c) => c.querySelector("strong")?.textContent === "Wi-Fi Connection");
const button = (el, text) => [...el.querySelectorAll("button")]
  .find((b) => b.textContent.trim() === text);
const dialog = () => document.querySelector("[data-switch-wifi]");
const apPosts = () => globalThis.__posts.filter(([u]) => u === "/api/hub/network/ap");
const reboots = () => globalThis.__posts.filter(([u]) => u === "/api/hub/panel/power");

// -- a + b. on the access point, switching to a saved network ------------------------
await render(base, async (box) => {
  const c = apCard(box);
  check("the card says the switch happens now and the hotspot never turns on by itself",
        c.textContent.includes("Switching happens straight away. The hotspot never turns on "
                               + "by itself, not even when the Wi-Fi cannot be joined.")
        && !c.textContent.includes("45 s") && !c.textContent.includes("comes back"),
        c.textContent);
  check("...and no longer that the switch is made when the box reboots",
        !c.textContent.includes("when the box reboots") && !c.textContent.includes("after reboot"));
  check("the Wi-Fi card no longer carries the 'Device Hotspot Active' note",
        !wifiCard(box).textContent.includes("Device Hotspot Active")
        && !wifiCard(box).textContent.includes("broadcasting its own Wi-Fi network")
        && !wifiCard(box).textContent.includes("hotspot stays off until you turn it on again")
        && !wifiCard(box).textContent.includes("comes back automatically")
        && !wifiCard(box).textContent.includes("45 seconds"), wifiCard(box).textContent);

  await click(apSwitch(c));
  check("the hotspot switch, off, opens the network picker (no reboot question)",
        !!dialog() && globalThis.__asked.length === 0);
  const pick = dialog().querySelector("[data-switch-pick]");
  check("...offering any saved network, each saved one, and another network",
        [...pick.options].map((o) => o.textContent).join("|")
          === "Any saved network in range|Plant|Office|Another network...",
        [...pick.options].map((o) => o.textContent).join("|"));
  check("...and saying the hotspot does NOT come back by itself",
        dialog().textContent.includes("If the box cannot join, the hotspot does NOT come back "
                                      + "by itself")
        && !dialog().textContent.includes("45 s"), dialog().textContent);
  await choose(pick, "Plant");
  globalThis.__answers.push(false);
  await click(button(dialog(), "Switch to Wi-Fi"));
  const asked = globalThis.__asked[0] || {};
  check("a confirm follows, titled for the switch", asked.title === "Switch to Wi-Fi now?",
        JSON.stringify(asked));
  const d = asked.description || "";
  check("...saying this page stops, since the browser is on the access point",
        d.includes("You are connected through this hotspot, so this page will stop responding"), d);
  check("...where to find the box afterwards: its name.local and its screen",
        d.includes("join Plant on this computer") && d.includes("http://iriv-abc.local:5100")
        && d.includes("the device's new address on its screen (wlan0)"), d);
  check("...and, plainly, that the hotspot will NOT come back by itself",
        d.includes("If it cannot connect, the hotspot will NOT come back by itself. Make sure "
                   + "you can reach this device another way (cable) or know the Wi-Fi password.")
        && !d.includes("45 s") && !d.includes("comes back by itself"), d);
  check("No posts nothing", apPosts().length === 0 && reboots().length === 0);

  await click(apSwitch(c));
  await choose(dialog().querySelector("[data-switch-pick]"), "Plant");
  globalThis.__answers.push(true);
  globalThis.__reply = { ...base, switch: { ...base.switch, state: "switching",
                                            message: "Switching wlan0 to Wi-Fi now." } };
  await click(button(dialog(), "Switch to Wi-Fi"));
  check("Yes posts {on:false, network} to network/ap",
        apPosts().length === 1 && apPosts()[0][1].on === false
        && apPosts()[0][1].network === "Plant", JSON.stringify(globalThis.__posts));
  check("...and never a reboot", reboots().length === 0);
  check("...the dialog closes", !dialog());
});

// The Wi-Fi card's switch opens the same picker; "any saved" posts no name.
await render({ ...base, via: "eth0" }, async (box) => {
  await click(wifiCard(box).querySelector("[role=switch]"));
  check("the Wi-Fi switch in AP mode opens the picker", !!dialog());
  globalThis.__answers.push(true);
  await click(button(dialog(), "Switch to Wi-Fi"));
  const d = (globalThis.__asked[0] || {}).description || "";
  check("from a cable the confirm says AP devices drop, not that this page does",
        d.includes("Devices on the hotspot are disconnected")
        && !d.includes("this page will stop responding")
        && d.includes("the hotspot will NOT come back by itself"), d);
  check("any saved network posts {on:false} with no name",
        apPosts().length === 1 && apPosts()[0][1].on === false
        && !("network" in apPosts()[0][1]) && !("ssid" in apPosts()[0][1]),
        JSON.stringify(apPosts()));
});

// -- c. no saved network: type one ----------------------------------------------------
await render({ ...base, saved: [] }, async (box) => {
  await click(apSwitch(apCard(box)));
  const dl = dialog();
  const go = button(dl, "Switch without testing");
  check("an older helper: no Test buttons, the switch says it is untested",
        !!go && !button(dl, "Test connection") && !button(dl, "Test and switch"));
  check("with nothing saved the picker asks for a name and password",
        !!dl.querySelector("[data-switch-ssid]") && !!dl.querySelector("[data-switch-password]"));
  check("...and will not switch until a name is typed", go.disabled);
  await type(dl.querySelector("[data-switch-ssid]"), "Guest Net");
  await type(dl.querySelector("[data-switch-password]"), "guestpass1");
  globalThis.__answers.push(true);
  await click(button(dialog(), "Switch without testing"));
  check("...then posts {on:false, ssid, password}",
        apPosts().length === 1 && apPosts()[0][1].ssid === "Guest Net"
        && apPosts()[0][1].password === "guestpass1", JSON.stringify(apPosts()));
});

// -- d. Wi-Fi -> access point -----------------------------------------------------------
await render(onWifi, async (box) => {
  const c = apCard(box);
  globalThis.__answers.push(false);
  await click(apSwitch(c));
  const asked = globalThis.__asked[0] || {};
  const d = asked.description || "";
  check("the hotspot switch, on, asks first, titled for the switch",
        asked.title === "Turn on the device hotspot now?", JSON.stringify(asked));
  check("...from a cable: now, no reboot, Ethernet not affected, the page stays",
        d.includes('broadcasts "iriv-abc"') && d.includes("No reboot; Ethernet is not affected")
        && !d.includes("stop responding"), d);
  check("...and says the Wi-Fi comes back if the AP cannot start",
        d.includes("the device goes back to the Wi-Fi network"), d);
  check("No posts nothing", apPosts().length === 0);
  globalThis.__answers.push(true);
  await click(apSwitch(c));
  check("Yes posts {on:true}, and no reboot",
        apPosts().length === 1 && apPosts()[0][1].on === true && reboots().length === 0,
        JSON.stringify(globalThis.__posts));
});
await render({ ...onWifi, via: "wlan0" }, async (box) => {
  globalThis.__answers.push(false);
  await click(apSwitch(apCard(box)));
  const d = (globalThis.__asked[0] || {}).description || "";
  check("from wlan0 the confirm says this page stops, which network to join and where",
        d.includes("this page will stop responding") && d.includes('join "iriv-abc"')
        && d.includes("http://10.42.0.1:5100") && d.includes("http://iriv-abc.local:5100"), d);
});

// -- e. under way, failed, followed ------------------------------------------------------
await render({ ...base, via: "eth0",
               switch: { ...base.switch, state: "switching",
                         message: "Switching wlan0 to Wi-Fi now." } }, async (box) => {
  const note = apCard(box).querySelector("[data-switch-note]");
  check("a switch under way shows on the card, with no hotspot promise",
        !!note && note.textContent.includes("Switching wlan0 to Wi-Fi now.")
        && !note.textContent.includes("45 s") && !note.textContent.includes("comes back"),
        note?.textContent);
  check("...and locks the switch controls",
        apSwitch(apCard(box)).disabled
        && wifiCard(box).querySelector("[role=switch]").disabled);
});
// -- e2. saved Wi-Fi but the hotspot is running (a hub start leaves it alone) ----------------
await render({ ...base, mode: "wifi", current_mode: "ap", mode_mismatch: true, ap: true },
             async (box) => {
  const note = apCard(box).querySelector("[data-switch-note]");
  check("saved Wi-Fi with the hotspot running says what is running and what to do",
        !!note && note.textContent.includes("Currently running on Hotspot")
        && note.textContent.includes("Disable the hotspot to enable Wi-Fi mode")
        && !note.textContent.includes("broadcasting"), note?.textContent);
});
const HOTSPOT_OFF = "The hotspot is off. Turn the hotspot on to reach this device without Wi-Fi.";
await render({ ...onWifi,
               devices: [base.devices[0], { device: "wlan0", type: "wifi",
                                            state: "disconnected", connection: "", ip: "" }],
               switch: { ...base.switch, state: "failed", reason: "wrong_password",
                         hotspot_off: true, ap_back: false,
                         finished: Date.now() / 1000 - 5,
                         message: "Could not connect to Plant: the password was not accepted. "
                                  + HOTSPOT_OFF } },
             async (box) => {
  const note = apCard(box).querySelector("[data-switch-note]");
  check("a switch that just failed shows its reason and that the hotspot is off",
        !!note && note.textContent.includes("the password was not accepted")
        && note.textContent.includes(HOTSPOT_OFF) && !note.textContent.includes("is back"),
        note?.textContent);
  check("...the hotspot switch shows Off (nothing raised it)",
        apSwitch(apCard(box)).getAttribute("aria-checked") === "false",
        apSwitch(apCard(box)).outerHTML);
  const on = note.querySelector("[data-hotspot-on]");
  check("...with a Turn Hotspot On button", !!on && on.textContent.trim() === "Turn Hotspot On");
  globalThis.__answers.push(false);
  await click(on);
  check("...which asks first (the manual switch's own confirm) and posts nothing on No",
        (globalThis.__asked[0] || {}).title === "Turn on the device hotspot now?"
        && apPosts().length === 0, JSON.stringify(globalThis.__asked));
  globalThis.__answers.push(true);
  await click(note.querySelector("[data-hotspot-on]"));
  check("...and on Yes posts {on:true}: the person turns it on, by hand",
        apPosts().length === 1 && apPosts()[0][1].on === true, JSON.stringify(apPosts()));
});
await render({ ...base, via: "eth0" }, async (box) => {
  globalThis.__answers.push(true);
  globalThis.__reply = { ...base, switch: { ...base.switch, state: "switching" } };
  await click(apSwitch(apCard(box)));
  await click(button(dialog(), "Switch to Wi-Fi"));
  globalThis.__status = { ...onWifi, switch: { ...base.switch, state: "done",
                                               message: "wlan0 joined Plant as 192.168.1.77." } };
  await act(async () => { await new Promise((r) => setTimeout(r, 2300)); });
  await flush();
  check("the page follows the switch to its outcome and says it",
        globalThis.__toasts.some(([k, , d]) => k === "success" && d.includes("192.168.1.77")),
        JSON.stringify(globalThis.__toasts));
  check("...and redraws in the new mode",
        apSwitch(apCard(box))?.getAttribute("aria-checked") === "false");
  check("no em dash anywhere on the page", !box.textContent.includes(String.fromCharCode(0x2014)));
});

// -- g. which redirect carries the session (Pipi M1) ----------------------------------------
global.sessionStorage = dom.window.sessionStorage;
sessionStorage.setItem("fx-session", "tok123");
{
  const loc = { protocol: "http:", port: "5100", pathname: "/settings", search: "?tab=network" };
  const own = { host: "10.42.0.1", own: true };
  const lease = { host: "192.168.1.61", own: false };
  check("on wlan0 (changing networks): no session, even to the box's own address",
        m.openUrl(own, { onWlan: true }, loc) === "http://10.42.0.1:5100/settings?tab=network"
        && !m.sessionCarriesTo("http://10.42.0.1:5100/settings?tab=network"));
  check("on a cable, to an address that is the box's own: the session travels",
        m.sessionCarriesTo(m.openUrl(own, { onWlan: false }, loc))
        && !m.openUrl(own, { onWlan: false }, loc).includes("tok123"));
  check("on a cable, to a predicted lease: no session",
        !m.sessionCarriesTo(m.openUrl(lease, { onWlan: false }, loc)));
  sessionStorage.removeItem("fx-session");
}

// -- f. the overlay finds the box again ----------------------------------------------------
await render(base, async (box) => {
  const probed = [];
  global.fetch = async (url) => {
    probed.push(url);
    const who = url.includes("192.168.1.77") ? { box: "b0x1d", switch: "done" }
      : url.includes("192.168.1.60") ? { box: "someone-else", switch: "done" } : null;
    if (!who) throw new Error("unreachable");
    return { ok: true, json: async () => who };
  };
  globalThis.__answers.push(true);
  globalThis.__reply = { ...base, box_id: "b0x1d",
    switch: { ...base.switch, state: "switching", candidates: [
      { host: "192.168.1.60", why: "its last DHCP lease on Plant", own: false },
      { host: "192.168.1.77", why: "the address it had on Plant before", own: false },
      { host: "iriv-abc.local", why: "its name on the network", own: false }] } };
  await click(apSwitch(apCard(box)));
  await click(button(dialog(), "Switch to Wi-Fi"));
  const ov = () => document.querySelector("[data-wifi-overlay]");
  check("the switch covers the whole window", !!ov(), document.body.innerHTML.slice(0, 200));
  check("...and that the user signs in once at the new address",
        ov()?.textContent.includes("asked to sign in once at the new address"), ov()?.textContent);
  check("...saying which network to join, and that a page cannot move the computer",
        ov()?.textContent.includes("join the Wi-Fi network Plant or Office")
        && ov()?.textContent.includes("cannot change which network"), ov()?.textContent);
  const key = new window.KeyboardEvent("keydown", { key: "r", bubbles: true, cancelable: true });
  document.body.dispatchEvent(key);
  check("...and swallows the keyboard behind it", key.defaultPrevented);
  globalThis.__getFail = true;                // the box has left this address
  await act(async () => { await new Promise((r) => setTimeout(r, 2300)); });
  await flush();
  const step = ov()?.querySelector("[data-wifi-step]")?.textContent || "";
  check("...probes every candidate on this page's port and path",
        ["192.168.1.60", "192.168.1.77", "iriv-abc.local"]
          .every((h) => probed.includes(`https://${h}/api/health/box`)), JSON.stringify(probed));
  check("...and opens only the one that answers as THIS box",
        step.includes("Found the box at 192.168.1.77") && !step.includes("192.168.1.60"), step);
  check("no em dash in the overlay", !ov()?.textContent.includes(String.fromCharCode(0x2014)));
  globalThis.__getFail = false;
  delete global.fetch;
});

// -- h. Search Wi-Fi, Test connection, Test and switch --------------------------------------
const testable = { ...base, saved: [], wifi_test: true };
const wait2 = async () => {
  await act(async () => { await new Promise((r) => setTimeout(r, 2300)); });
  await flush();
};
const wifiPosts = (url) => globalThis.__posts.filter(([u]) => u === url);
await render(testable, async (box) => {
  await click(apSwitch(apCard(box)));
  globalThis.__reply = { wifi: [
    { ssid: "Plant", signal: 80, secure: true, security: "WPA2", chan: "6" },
    { ssid: "Corp", signal: 70, secure: true, security: "WPA2 802.1X", enterprise: true },
    { ssid: "Cafe", signal: 40, secure: false, security: "" }], fresh: true };
  await click(dialog().querySelector("[data-wifi-search]"));
  const rows = [...dialog().querySelectorAll("[data-wifi-row]")];
  check("Search Wi-Fi posts the scan and lists each network with security and signal",
        wifiPosts("/api/hub/network/scan").length === 1 && rows.length === 3
        && rows[0].textContent.includes("Plant") && rows[0].textContent.includes("WPA2")
        && rows[0].textContent.includes("80%") && rows[2].textContent.includes("Open"),
        dialog().textContent);
  check("...and says the access point stays on, and moves nothing", apPosts().length === 0
        && dialog().textContent.includes("The access point stays on while the box searches"));
  check("...an enterprise network is shown but cannot be picked",
        rows[1].disabled && rows[1].textContent.includes("not supported"));
  await click(rows[0]);
  check("picking a row fills the name", dialog().querySelector("[data-switch-ssid]").value === "Plant");
  check("...both Test buttons, locked until the secure network's password is in",
        button(dialog(), "Test connection").disabled && button(dialog(), "Test and switch").disabled);
  await type(dialog().querySelector("[data-switch-password]"), "short");
  check("...a too-short password is said, and still locked",
        dialog().textContent.includes("at least 8 characters")
        && button(dialog(), "Test connection").disabled);
  await type(dialog().querySelector("[data-switch-password]"), "goodpass1");
  globalThis.__answers.push(false);
  await click(button(dialog(), "Test connection"));
  const asked = globalThis.__asked[globalThis.__asked.length - 1] || {};
  check("Test connection asks first: the AP goes off, this computer drops and rejoins",
        asked.title === "Test this Wi-Fi network?"
        && asked.description.includes('stops broadcasting "iriv-abc"')
        && asked.description.includes("Nothing is saved")
        && asked.description.includes("normally rejoins it by itself"), JSON.stringify(asked));
  check("...No posts nothing", wifiPosts("/api/hub/network/wifi/test").length === 0);
  globalThis.__answers.push(true);
  globalThis.__reply = { ...testable, switch: { ...base.switch, state: "switching", to: "test",
                                                message: "Testing Plant now." } };
  await click(button(dialog(), "Test connection"));
  const sentTest = wifiPosts("/api/hub/network/wifi/test");
  check("Yes posts {ssid, password} to network/wifi/test, not a switch",
        sentTest.length === 1 && sentTest[0][1].ssid === "Plant"
        && sentTest[0][1].password === "goodpass1" && apPosts().length === 0,
        JSON.stringify(globalThis.__posts));
  const ov = () => document.querySelector("[data-wifi-overlay]");
  check("...the page is covered while the AP is off, saying so",
        ov()?.textContent.includes("Testing the Wi-Fi network")
        && !!ov()?.querySelector("[data-wifi-test-note]"), ov()?.textContent);
  globalThis.__status = { ...testable, switch: { ...base.switch, state: "done", test: true,
    message: "Wi-Fi connection is ready: the box joined Plant and was given 192.168.1.57. "
             + "The access point iriv-abc is back; nothing was saved." } };
  await wait2();
  const ok = dialog()?.querySelector("[data-switch-outcome]");
  check("the test over: the overlay goes, the dialog says ready with the address",
        !ov() && ok?.dataset.switchOutcome === "ok"
        && ok.textContent.includes("Wi-Fi connection is ready")
        && ok.textContent.includes("192.168.1.57"), dialog()?.textContent);
  check("...and keeps what was typed, for Test and switch",
        dialog().querySelector("[data-switch-ssid]").value === "Plant"
        && dialog().querySelector("[data-switch-password]").value === "goodpass1"
        && !button(dialog(), "Test and switch").disabled);
});

await render(testable, async (box) => {
  await click(apSwitch(apCard(box)));
  await type(dialog().querySelector("[data-switch-ssid]"), "Plant");
  await type(dialog().querySelector("[data-switch-password]"), "badpass12");
  await click(dialog().querySelector("[data-switch-hidden]"));
  globalThis.__answers.push(true);
  globalThis.__reply = { ...testable, switch: { ...base.switch, state: "switching", to: "test" } };
  await click(button(dialog(), "Test connection"));
  check("a hidden network is tested as hidden",
        wifiPosts("/api/hub/network/wifi/test")[0]?.[1].hidden === true);
  globalThis.__status = { ...testable, switch: { ...base.switch, state: "failed",
    reason: "wrong_password", message: "Could not join Plant: the password was not accepted. "
      + "The access point iriv-abc is back; nothing was saved." } };
  await wait2();
  const out = dialog()?.querySelector("[data-switch-outcome]");
  check("a failed test: the reason in the dialog, and to correct it and retry",
        out?.dataset.switchOutcome === "failed"
        && out.textContent.includes("the password was not accepted")
        && out.textContent.includes("Correct it and try again"), dialog()?.textContent);
  check("...nothing typed is lost",
        dialog().querySelector("[data-switch-password]").value === "badpass12"
        && dialog().querySelector("[data-switch-hidden]").checked);
});

await render({ ...testable, via: "eth0" }, async (box) => {
  await click(apSwitch(apCard(box)));
  await type(dialog().querySelector("[data-switch-ssid]"), "Plant");
  await type(dialog().querySelector("[data-switch-password]"), "badpass12");
  globalThis.__answers.push(true);
  globalThis.__reply = { ...testable, switch: { ...base.switch, state: "switching" } };
  await click(button(dialog(), "Test and switch"));
  check("Test and switch is the switch: {on:false, ssid, password} to network/ap",
        apPosts().length === 1 && apPosts()[0][1].ssid === "Plant"
        && apPosts()[0][1].password === "badpass12"
        && wifiPosts("/api/hub/network/wifi/test").length === 0, JSON.stringify(apPosts()));
  check("...the dialog gives way to the overlay", !dialog()
        && !!document.querySelector("[data-wifi-overlay]"));
  globalThis.__status = { ...testable, via: "eth0", ap: "", switch: { ...base.switch,
    state: "failed", reason: "assoc_failed", hotspot_off: true, ap_back: false,
    message: "Could not connect to Plant: the box found the network but "
      + "could not connect to it (weak signal, or the router refused the box). "
      + HOTSPOT_OFF } };
  await wait2();
  const out = dialog()?.querySelector("[data-switch-outcome]");
  check("a failed Test and switch reopens the dialog with the reason, filled in",
        !!out && out.textContent.includes("weak signal")
        && dialog().querySelector("[data-switch-ssid]").value === "Plant"
        && dialog().querySelector("[data-switch-password]").value === "badpass12"
        && out.textContent.includes(HOTSPOT_OFF), dialog()?.textContent);
  check("no em dash in the dialog", !dialog().textContent.includes(String.fromCharCode(0x2014)));
});

// -- i. no product text promises a hotspot that comes back by itself ------------------
// (Tok Aba, 2026-10-14.) Test connection may still say the hotspot comes back
// AFTER A TEST: the person never left it. Nothing else may.
{
  const { readFileSync } = await import("node:fs");
  for (const name of ["NetworkPanel.jsx", "SwitchWifiDialog.jsx", "WifiSwitchOverlay.jsx"]) {
    const src = readFileSync(join(FRONT, "src", "components", name), "utf-8");
    check(`${name} promises no automatic hotspot`,
          !/comes back (automatically|by itself|within)|back within 45|within 45 s/i.test(src)
          && !src.includes("If the Wi-Fi cannot be joined the hotspot")
          && !src.includes("wifi_loss_hotspot"));
  }
}

if (bad) {
  console.error(`\n${bad} check(s) failed`);
  process.exit(1);
}
console.log("\nall Wi-Fi switch page checks passed");
dom.window.close();
process.exit(0);
