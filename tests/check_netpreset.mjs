// The Network page: a wired jack with no cable can be set ahead of time, and a
// fixed address on a connected one asks Reboot now / Later.
//
// Beta feedback 5c: in access-point mode eth0/eth1 read "No connection profile
// · unavailable" and the card was locked with "Connect a cable or join Wi-Fi
// before changing IPv4" -- an installer could not set the address the site
// wants before the cable went in. Item 4: a fixed address on a connected
// interface was "Apply and reboot" in one press, with no "not now".
// Pinned, by rendering NetworkPanel against a shimmed api:
//   1. an unplugged wired card offers DHCP / static with "will apply when a
//      cable is connected", not the lock; its button is Save; saving posts
//      the address with no confirm and no reboot
//   2. a connected card's fixed address: confirm, apply, then Reboot now /
//      Later -- Later posts no reboot, Reboot now does
//   3. a "Later" reboot shows a pending note on that card
//   4. an unmanaged jack and an unjoined wlan0 stay locked
//   5. (Tok Aba 2026-10-09) no uplink tick: the gateway is optional and
//      checked while typing, one line under it says the auto rule, a gateway
//      beside another port's default route says that port keeps the internet,
//      nothing posts `uplink`; the port the internet leaves by now is badged
//      Internet. DNS: an optional field on both modes, checked while typing,
//      posted as typed; on DHCP it is the only change Apply can make; the
//      DNS in use is shown
//   7. (Tok Aba 2026-10-09) the address Test says conflict in red with the
//      other device's MAC, free in green, this box's own address plainly,
//      next to the IP field; Apply asks the same question first and stops
//      on a conflict, with nothing posted; a conflict blocks Apply until the
//      address changes
//   6. (Pipi M2) a static uplink the box could not reach the internet
//      through asks "Keep this setting?" with the box's countdown; Keep and
//      Revert now post for that port; while the check runs, Apply does not
//      ask about a reboot (it would end the check)
//   8. (Tok Aba 2026-10-14) the card's new look: the header says Connected /
//      Not connected, "Automatic IP address (DHCP)" carries an On / Off
//      switch at its right, "Current IP address" shows the address big,
//      "Advanced Network Settings" (IP address, Subnet mask, Default gateway
//      (optional), Preferred / Alternate DNS server) folds, and the one
//      button is "Save Network Settings"; a customer does not see the
//      profile line, the lease detail, DNS in use, the Internet badge or the
//      field help (a developer does: those cases render with an account
//      that is_developer); switching DHCP on posts dhcp: true
//
//   node tests/check_netpreset.mjs
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".netpreset-test.cjs");

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
// the same status (the real routes return the full status on every write);
// the address Test answers globalThis.__test when a check sets one.
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
                            if (url.endsWith("/ipv4/test") && globalThis.__test)
                              return Promise.resolve(globalThis.__test);
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
// globalThis.__dev: render as a developer account (the extras show).
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

// -- 1. the unplugged jack ----------------------------------------------------------
await render(base, async (box) => {
  const eth1 = card(box, "eth1");
  check("eth1 has a card", !!eth1);
  check("...that no longer locks with 'connect a cable first'",
        !eth1.textContent.includes("Connect a cable or join Wi-Fi before"), eth1.textContent);
  check("...and says the setting will apply when a cable is connected",
        eth1.textContent.includes("will apply when") && eth1.textContent.includes("cable is connected")
        && eth1.textContent.includes("no reboot is needed"), eth1.textContent);
  // Customer copy pass (2026-10-11).
  check("...titled Ethernet Connection 2 (eth1), with the no-cable sentence",
        eth1.querySelector("strong")?.textContent === "Ethernet Connection 2 (eth1)"
        && eth1.querySelector("[data-no-cable]")?.textContent.replace(/\s+/g, " ").includes(
          "No network cable detected. Saved settings will apply when a cable is connected."));
  check("...DHCP named the spec's way",
        eth1.textContent.includes("Automatic IP address (DHCP)")
        && eth1.textContent.includes("Get an IP address automatically from your network."));
  const adv = eth1.querySelector("[data-net-advanced]");
  check("...DNS sits under a closed Advanced Network Settings while it is empty",
        !!adv && adv.tagName === "DETAILS" && !adv.open
        && adv.querySelector("summary")?.textContent === "Advanced Network Settings"
        && !!adv.querySelector("[data-dns-field]"));
  const sw = eth1.querySelector('[aria-label="eth1 uses DHCP"]');
  check("...with the DHCP switch on it", !!sw);
  await click(sw);
  const inputs = [...eth1.querySelectorAll("input.input")];
  check("switching DHCP off opens the address, mask and gateway fields", inputs.length >= 3,
        inputs.length);
  await type(inputs[0], "192.168.50.10");
  await type(inputs[3] || inputs[2], "192.168.50.1");
  await flush();
  // Tok Aba (2026-10-09): no uplink tick; the box decides (section 5).
  check("the gateway is inside Advanced Network Settings, which is open with DHCP off",
        eth1.querySelector("[data-net-advanced]").contains(inputs[3])
        && eth1.querySelector("[data-net-advanced]").open);
  check("the gateway is optional, with no uplink tick",
        eth1.textContent.includes("Default gateway (optional)")
        && !eth1.textContent.includes("This port is the internet uplink")
        && !eth1.querySelector('input[type="checkbox"]'), eth1.textContent);
  const save = button(eth1, "Save Network Settings");
  check("the button is Save, not Apply and reboot",
        !!save && !button(eth1, "Apply and reboot") && !button(eth1, "Apply Network Settings"));
  check("...enabled once the address is valid", save && !save.disabled);
  await click(save);
  const posts = globalThis.__posts;
  const ipv4 = posts.filter(([u]) => u === "/api/hub/network/ipv4");
  check("Save posts the address for eth1, static",
        ipv4.length === 1 && ipv4[0][1].device === "eth1" && ipv4[0][1].dhcp === false
        && ipv4[0][1].ip === "192.168.50.10" && ipv4[0][1].mask === "255.255.255.0",
        JSON.stringify(posts));
  check("...with no confirm and NO reboot", globalThis.__asked.length === 0
        && !posts.some(([u]) => u === "/api/hub/panel/power"), JSON.stringify(posts));

  const eth2 = card(box, "eth2");
  check("an unmanaged jack stays locked, and says why",
        eth2 && eth2.textContent.includes("does not manage")
        && !eth2.querySelector('[aria-label="eth2 uses DHCP"]'), eth2?.textContent);
});

// A saved preset reads back on the card (the summary is a developer's).
globalThis.__dev = true;
await render({ ...base, devices: base.devices.map((d) => (d.device !== "eth1" ? d : {
  ...d, profile: "Wired eth1", method: "manual", dhcp: false,
  static_ip: "192.168.50.10", static_prefix: "24", static_gateway: "192.168.50.1",
})) }, async (box) => {
  const eth1 = card(box, "eth1");
  check("a saved preset shows its profile and the address it will take",
        eth1.textContent.includes("Wired eth1 (saved)")
        && eth1.textContent.includes("192.168.50.10/24")
        && eth1.textContent.includes("STATIC"), eth1.textContent);
});
globalThis.__dev = false;

// -- 2. a connected interface: Apply, then Reboot now / Later -------------------------
async function applyStatic(answers) {
  return render(base, async (box) => {
    const eth0 = card(box, "eth0");
    await click(eth0.querySelector('[aria-label="eth0 uses DHCP"]'));
    globalThis.__answers.push(...answers);
    const apply = button(eth0, "Save Network Settings");
    check("a connected card's button is Save Network Settings (no 'and reboot')",
          !!apply && !button(eth0, "Apply and reboot") && !button(eth0, "Apply Network Settings"));
    await click(apply);
    return { asked: [...globalThis.__asked], posts: [...globalThis.__posts] };
  });
}
let r = await applyStatic([true, false]);
check("Apply asks first, naming the address taken from the lease",
      r.asked[0]?.title?.includes("192.168.1.116"), JSON.stringify(r.asked[0]));
check("...posts the address",
      r.posts.some(([u, b]) => u === "/api/hub/network/ipv4" && b.device === "eth0"
                   && b.dhcp === false));
check("...then asks Reboot now / Later, like the Wi-Fi / AP switch",
      r.asked[1]?.confirmLabel === "Reboot now" && r.asked[1]?.cancelLabel === "Later",
      JSON.stringify(r.asked[1]));
check("Later: no reboot is posted",
      !r.posts.some(([u]) => u === "/api/hub/panel/power"), JSON.stringify(r.posts));
r = await applyStatic([true, true]);
check("Reboot now: the reboot is posted",
      r.posts.some(([u, b]) => u === "/api/hub/panel/power" && b.action === "reboot"),
      JSON.stringify(r.posts));

// -- 3. the note "Later" leaves behind ----------------------------------------------
await render({ ...base, ipv4_reboot: ["eth0"] }, async (box) => {
  const eth0 = card(box, "eth0");
  const eth1 = card(box, "eth1");
  check("a reboot left for later shows on that card",
        eth0.textContent.includes("picks it") && eth0.textContent.includes("next reboot")
        && !!button(eth0, "Reboot now"), eth0.textContent);
  check("...and only on that card", !eth1.textContent.includes("next reboot"));
});

// -- 4. wlan0 not joined is still locked (its story is join / AP) -----------------
await render({ ...base, ap: "", mode: "wifi", current_mode: "wifi", via: "eth0",
               devices: base.devices.map((d) => (d.device !== "wlan0" ? d
                 : { ...d, state: "disconnected", connection: "", ip: "" })) },
async (box) => {
  const text = box.textContent;
  check("an unjoined wlan0 keeps the lock message",
        text.includes("Connect a cable or join Wi-Fi before")
        && !box.querySelector('[aria-label="wlan0 uses DHCP"]'));
  // Customer copy pass (2026-10-11): page heading, and the warning on the port
  // this browser rides on.
  check("the page is Network Settings, with the spec subtitle",
        box.querySelector("h1")?.textContent === "Network Settings"
        && box.textContent.replace(/\s+/g, " ").includes(
          "Connect this device to your network, configure its network address, and manage secure access to the dashboard."));
  const eth0 = card(box, "eth0");
  check("...eth0 is Ethernet Connection 1 and says you are connected through it",
        eth0?.querySelector("strong")?.textContent === "Ethernet Connection 1 (eth0)"
        && eth0.querySelector("[data-via-warning] b")?.textContent === "You Are Connected Through This Port"
        && eth0.querySelector("[data-via-warning]")?.textContent.replace(/\s+/g, " ").includes(
          "Changing these settings may disconnect your current session."));
});

// -- 5. the uplink is the box's call; DNS ---------------------------------------------
const withDefault = { ...base, devices: base.devices.map((d) => (d.device === "eth0"
  ? { ...d, has_default: true, default_route: true, default_metric: 100,
      dns: ["192.168.1.1", "8.8.8.8"] } : d)) };
const gwField = (el) => [...el.querySelectorAll("input.input")][3];
const dnsField = (el) => el.querySelector('[data-dns-field="1"]');
const dnsField2 = (el) => el.querySelector('[data-dns-field="2"]');
globalThis.__dev = true;
await render(withDefault, async (box) => {
  const eth0 = card(box, "eth0");
  check("the port the internet leaves by now is badged Internet (developer)",
        !!eth0.querySelector("[data-internet-route]")
        && !card(box, "eth1").querySelector("[data-internet-route]"));
  check("...and its card says the DNS in use",
        eth0.querySelector("[data-dns-in-use]")?.textContent.includes("192.168.1.1, 8.8.8.8"),
        eth0.textContent);
  const eth1 = card(box, "eth1");
  await click(eth1.querySelector('[aria-label="eth1 uses DHCP"]'));
  const inputs = [...eth1.querySelectorAll("input.input")];
  await type(inputs[0], "10.0.0.10");
  await type(gwField(eth1), "");
  await flush();
  check("no gateway is fine: Save is offered, no problem under the field",
        !button(eth1, "Save Network Settings").disabled && !eth1.querySelector("[data-gateway-problem]")
        && eth1.querySelector("[data-gateway-rule]")?.textContent.includes("machine-only"),
        eth1.textContent);
  check("...and with no gateway, no word about eth0's internet",
        !eth1.textContent.includes("already has a route to the internet"), eth1.textContent);
  await type(gwField(eth1), "10.0.0.10");
  await flush();
  check("the box's own address as gateway is refused while typing (board .47)",
        button(eth1, "Save Network Settings").disabled
        && eth1.querySelector("[data-gateway-problem]")?.textContent.includes("own address"));
  await type(gwField(eth1), "10.0.1.1");
  await flush();
  check("...and a gateway outside the subnet",
        button(eth1, "Save Network Settings").disabled
        && eth1.querySelector("[data-gateway-problem]")?.textContent.includes("outside"));
  await type(gwField(eth1), "10.0.0.1");
  await flush();
  check("a gateway beside eth0's route: eth0 keeps the internet if it works, eth1 is the "
        + "fallback or machine-only",
eth1.querySelector("[data-routing-notice]")?.textContent.includes("Network Routing Notice")
        && eth1.textContent.includes("eth0 already has a route to the internet")
        && eth1.textContent.includes("fallback")
        && eth1.textContent.includes("machine-only network if the internet does not answer"),
        eth1.textContent);
  check("two DNS boxes, Preferred and Alternate",
        eth1.textContent.includes("Preferred DNS server")
        && eth1.textContent.includes("Alternate DNS server (optional)") && !!dnsField2(eth1));
  await type(dnsField(eth1), "8.8.8.8");
  await type(dnsField2(eth1), "1.1.1");
  await flush();
  check("a bad DNS server: Save waits and the field says which",
        button(eth1, "Save Network Settings").disabled
        && eth1.querySelector("[data-dns-problem]")?.textContent.includes("1.1.1 is not"),
        eth1.textContent);
  await type(dnsField(eth1), "127.0.0.1");
  await flush();
  check("...loopback is no resolver",
        eth1.querySelector("[data-dns-problem]")?.textContent.includes("cannot be a DNS"));
  await type(dnsField(eth1), "8.8.8.8");
  await type(dnsField2(eth1), "8.8.8.8");
  await flush();
  check("...nor the same server twice", button(eth1, "Save Network Settings").disabled
        && eth1.querySelector("[data-dns-problem]")?.textContent.includes("Same as the preferred"));
  await type(dnsField2(eth1), "1.1.1.1");
  await flush();
  await click(button(eth1, "Save Network Settings"));
  const post = globalThis.__posts.find(([u]) => u === "/api/hub/network/ipv4");
  check("Save posts the gateway and the DNS as typed, and no uplink field",
        post && post[1].gateway === "10.0.0.1" && post[1].dns === "8.8.8.8, 1.1.1.1"
        && !("uplink" in post[1]), JSON.stringify(globalThis.__posts));
});
globalThis.__dev = false;
// DHCP: the DNS override is the one thing Apply changes.
await render(withDefault, async (box) => {
  const eth0 = card(box, "eth0");
  check("a DHCP card has the DNS field, blank (the lease's), and Apply waits",
        dnsField(eth0)?.value === "" && button(eth0, "Save Network Settings").disabled, eth0.textContent);
  await type(dnsField(eth0), "1.1.1.1");
  await flush();
  check("...a server typed: Apply is offered", !button(eth0, "Save Network Settings").disabled);
  globalThis.__answers.push(true);
  await click(button(eth0, "Save Network Settings"));
  const post = globalThis.__posts.find(([u]) => u === "/api/hub/network/ipv4");
  check("...and posts DHCP with the server",
        post && post[1].dhcp === true && post[1].dns === "1.1.1.1",
        JSON.stringify(globalThis.__posts));
});
await render({ ...withDefault, devices: withDefault.devices.map((d) => (d.device !== "eth0" ? d
  : { ...d, dns_override: true, static_dns: "1.1.1.1" })) }, async (box) => {
  const eth0 = card(box, "eth0");
  check("a saved DHCP override opens filled in, and Apply waits until it changes",
        dnsField(eth0)?.value === "1.1.1.1" && dnsField2(eth0)?.value === ""
        && button(eth0, "Save Network Settings").disabled);
  await type(dnsField(eth0), "");
  await flush();
  check("...clearing it is a change (back to the lease's)", !button(eth0, "Save Network Settings").disabled);
});
// An old saved machine-only port still opens with its values; nothing is ticked.
await render({ ...withDefault, devices: withDefault.devices.map((d) => (d.device !== "eth1" ? d : {
  ...d, profile: "Wired eth1", method: "manual", dhcp: false, never_default: true,
  static_ip: "192.168.4.5", static_prefix: "24", static_gateway: "", static_dns: "",
})) }, async (box) => {
  const eth1 = card(box, "eth1");
  check("a port saved under the old tick opens with its address and no checkbox",
        [...eth1.querySelectorAll("input.input")][0]?.value === "192.168.4.5"
        && !eth1.querySelector('input[type="checkbox"]') && !button(eth1, "Save Network Settings").disabled,
        eth1.textContent);
});

// -- 7. IP conflict ------------------------------------------------------------------
const ipRow = (el) => [...el.querySelectorAll("input.input")][0].closest("label");
const testIs = (el) => el.querySelector("[data-ip-test]");
await render(base, async (box) => {
  const eth0 = card(box, "eth0");
  await click(eth0.querySelector('[aria-label="eth0 uses DHCP"]'));
  await type([...eth0.querySelectorAll("input.input")][0], "192.168.1.50");
  await flush();
  globalThis.__test = { ok: true, ip: "192.168.1.50", in_use: true, checked: true, own: false,
    mac: "aa:bb:cc:dd:ee:ff",
    detail: "IP conflict: 192.168.1.50 is already used by another device (aa:bb:cc:dd:ee:ff)" };
  await click(button(eth0, "Test"));
  const t = testIs(eth0);
  check("Test, conflict: red, says IP conflict with the MAC, inside the IP field's row",
        t?.dataset.ipTest === "conflict" && t.textContent.includes("IP conflict: 192.168.1.50")
        && t.textContent.includes("aa:bb:cc:dd:ee:ff") && ipRow(eth0).contains(t)
        && /dc2626|alert/.test(t.getAttribute("style")), t?.outerHTML);
  check("...and Apply is blocked until the address changes", button(eth0, "Save Network Settings").disabled);
  await type([...eth0.querySelectorAll("input.input")][0], "192.168.1.51");
  await flush();
  check("...editing the address clears the verdict and frees Apply",
        !testIs(eth0) && !button(eth0, "Save Network Settings").disabled);
  globalThis.__test = { ok: true, ip: "192.168.1.51", in_use: false, checked: true, own: false,
    detail: "No conflict: 192.168.1.51 is free" };
  await click(button(eth0, "Test"));
  check("Test, free: green, 'No conflict: <ip> is free'",
        testIs(eth0)?.dataset.ipTest === "free"
        && testIs(eth0).textContent.includes("No conflict: 192.168.1.51 is free")
        && /ok-text/.test(testIs(eth0).getAttribute("style")), testIs(eth0)?.outerHTML);
  globalThis.__test = { ok: true, ip: "192.168.1.116", in_use: false, checked: true, own: true,
    detail: "192.168.1.116 is already this box's address" };
  await type([...eth0.querySelectorAll("input.input")][0], "192.168.1.116");
  await click(button(eth0, "Test"));
  check("Test, this box's own address: a plain note, not a conflict, Apply allowed",
        testIs(eth0)?.dataset.ipTest === "own" && !button(eth0, "Save Network Settings").disabled
        && !/dc2626|alert/.test(testIs(eth0).getAttribute("style")), testIs(eth0)?.outerHTML);
});
await render(base, async (box) => {
  const eth0 = card(box, "eth0");
  await click(eth0.querySelector('[aria-label="eth0 uses DHCP"]'));
  await type([...eth0.querySelectorAll("input.input")][0], "192.168.1.50");
  await flush();
  globalThis.__test = { ok: true, ip: "192.168.1.50", in_use: true, checked: true, own: false,
    mac: "", detail: "IP conflict: 192.168.1.50 is already used by another device" };
  globalThis.__answers.push(true, false);
  await click(button(eth0, "Save Network Settings"));
  const posts = globalThis.__posts.map(([u]) => u);
  check("Apply runs the conflict check first and stops on a conflict: no confirm, "
        + "nothing applied",
        posts.includes("/api/hub/network/ipv4/test") && !posts.includes("/api/hub/network/ipv4")
        && globalThis.__asked.length === 0 && testIs(eth0)?.dataset.ipTest === "conflict",
        JSON.stringify(posts));
  globalThis.__test = null;
});

// -- 8. the 2026-10-14 look, as a customer sees it ---------------------------------
await render(withDefault, async (box) => {
  const eth0 = card(box, "eth0");
  const t = eth0.textContent.replace(/\s+/g, " ");
  check("8. header: title, Wired network connection, a green Connected pill",
        eth0.querySelector("strong")?.textContent === "Ethernet Connection 1 (eth0)"
        && t.includes("Wired network connection")
        && eth0.querySelector("[data-net-state]")?.textContent.trim() === "Connected"
        && eth0.querySelector("[data-net-state]").className === "state-pill is-on");
  check("...and grey Not connected on the bare jack",
        card(box, "eth1").querySelector("[data-net-state]")?.textContent.trim() === "Not connected"
        && card(box, "eth1").querySelector("[data-net-state]").className === "state-pill");
  const row = eth0.querySelector("[data-net-dhcp]");
  const pair = row?.querySelector(".state-switch");
  check("...Automatic IP address (DHCP) with an On label left of its switch",
        row?.textContent.includes("Automatic IP address (DHCP)")
        && row.textContent.includes("Get an IP address automatically from your network.")
        && pair?.querySelector("[data-state-label]")?.textContent === "On"
        && pair.querySelector("[data-state-label]").nextElementSibling
          === eth0.querySelector('[aria-label="eth0 uses DHCP"]'));
  check("...Current IP address, the address in large type",
        eth0.querySelector("[data-net-current]")?.textContent.replace(/\s+/g, " ")
          .includes("Current IP address192.168.1.116"));
  const adv = eth0.querySelector("[data-net-advanced]");
  check("...Advanced Network Settings closed on DHCP, holding the five fields",
        adv && !adv.open && adv.querySelector("summary")?.textContent === "Advanced Network Settings"
        && ["IP address", "Subnet mask", "Default gateway (optional)", "Preferred DNS server",
            "Alternate DNS server (optional)"].every((l) => adv.textContent.includes(l))
        && adv.textContent.includes("Shown when DHCP is disabled.")
        && dnsField(eth0)?.getAttribute("placeholder") === "e.g. 8.8.8.8"
        && dnsField2(eth0)?.getAttribute("placeholder") === "e.g. 1.1.1.1");
  const ins = [...adv.querySelectorAll("input.input")];
  check("...on DHCP the address, mask and gateway are disabled, DNS is not",
        ins[0].disabled && ins[1].disabled && ins[3].disabled
        && !dnsField(eth0).disabled && !dnsField2(eth0).disabled);
  check("...the one button is Save Network Settings",
        !!button(eth0, "Save Network Settings") && !button(eth0, "Apply Network Settings"));
  check("...and a customer does not see the developer extras",
        !eth0.querySelector("[data-net-dev-extras]") && !eth0.querySelector("[data-internet-route]")
        && !eth0.querySelector("[data-dns-in-use]") && !t.includes("Current lease")
        && !t.includes("Wired connection 1") && !eth0.querySelector("[data-gateway-rule]"), t);
  await click(eth0.querySelector('[aria-label="eth0 uses DHCP"]'));
  check("switching DHCP off: Off, the fields enabled, Advanced opens",
        pair.querySelector("[data-state-label]").textContent === "Off"
        && !ins[0].disabled && !ins[1].disabled && !ins[3].disabled && adv.open);
});
await render({ ...base, devices: base.devices.map((d) => (d.device !== "eth1" ? d : {
  ...d, profile: "Wired eth1", method: "manual", dhcp: false,
  static_ip: "192.168.50.10", static_prefix: "24", static_gateway: "",
})) }, async (box) => {
  const eth1 = card(box, "eth1");
  check("a fixed-address card opens with Off and Advanced open",
        eth1.querySelector("[data-net-dhcp] [data-state-label]")?.textContent === "Off"
        && eth1.querySelector("[data-net-advanced]").open);
  await click(eth1.querySelector('[aria-label="eth1 uses DHCP"]'));
  await click(button(eth1, "Save Network Settings"));
  const post = globalThis.__posts.find(([u]) => u === "/api/hub/network/ipv4");
  check("...switching DHCP on and saving posts dhcp: true for that port",
        post && post[1].device === "eth1" && post[1].dhcp === true, JSON.stringify(globalThis.__posts));
});

// A <details> whose `open` React controls and onToggle feeds back locked the
// page in a render loop when its summary was clicked (Chrome, 2026-10-14):
// Advanced Network Settings is a plain <details> opened from an effect.
{
  const { readFileSync } = await import("node:fs");
  const src = readFileSync(join(FRONT, "src", "components", "NetworkPanel.jsx"), "utf8");
  const tag = src.slice(src.lastIndexOf("<details", src.indexOf('data-net-advanced="1"')),
                        src.indexOf('data-net-advanced="1"'));
  check("Advanced Network Settings is not a controlled <details> (no open= / onToggle=)",
        tag.startsWith("<details") && !/\bopen=\{/.test(tag) && !/onToggle=/.test(tag), tag);
}

// -- 6. the uplink check (Pipi M2) ---------------------------------------------------
await render({ ...base, uplink_check: { eth0: { state: "pending", device: "eth0",
  gateway: "192.168.1.254", started: 1, remaining_s: 42,
  message: "Internet not reachable through eth0 (gateway 192.168.1.254). Keep this setting?" } } },
async () => {
  const dlg = document.querySelector("[data-uplink-keep]");
  check("a pending check asks, naming the port and gateway, with the box's countdown",
        !!dlg && dlg.textContent.includes("eth0") && dlg.textContent.includes("192.168.1.254")
        && document.querySelector("[data-uplink-left]")?.textContent === "42"
        && document.body.textContent.includes("Internet not reachable through this port"),
        dlg?.textContent);
  check("...saying the box reverts by itself, page or not",
        dlg.textContent.includes("goes back to its previous settings")
        && dlg.textContent.includes("even if"));
  const btn = (t) => [...document.querySelectorAll(".ui-dialog button")]
    .find((b) => b.textContent.trim() === t);
  await click(btn("Keep"));
  check("Keep posts ipv4/keep for that port",
        globalThis.__posts.some(([u, b]) => u === "/api/hub/network/ipv4/keep" && b.device === "eth0"),
        JSON.stringify(globalThis.__posts));
  globalThis.__posts.length = 0;
  await click(btn("Revert now"));
  check("Revert now posts ipv4/revert for that port",
        globalThis.__posts.some(([u, b]) => u === "/api/hub/network/ipv4/revert" && b.device === "eth0"),
        JSON.stringify(globalThis.__posts));
});
await render(base, async (box) => {
  const eth0 = card(box, "eth0");
  await click(eth0.querySelector('[aria-label="eth0 uses DHCP"]'));
  globalThis.__status = { ...base, uplink_check: { eth0: { state: "checking", started: 2 } } };
  globalThis.__answers.push(true);
  await click(button(eth0, "Save Network Settings"));
  check("while the box checks the internet, Apply asks no reboot question",
        globalThis.__asked.length === 1 && !globalThis.__asked.some((a) => a.title === "Reboot now?"),
        JSON.stringify(globalThis.__asked.map((a) => a.title)));
});

if (bad) {
  console.error(`\n${bad} check(s) failed`);
  process.exit(1);
}
console.log("\nall network preset checks passed");
dom.window.close();
process.exit(0);
