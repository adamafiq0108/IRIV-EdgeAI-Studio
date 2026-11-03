// The Software port and Hostname cards: saved, not applied; one restart for both.
//
// Tok Aba (2026-10-05): no terminal or sudo command shown to the user; saving
// does not take effect; the page says a restart is needed with Restart now /
// Later; both the current and the pending value are shown; the address after
// the restart is named before restarting. Pinned, by rendering both cards
// against a shimmed api:
//   1. a pending port reads "Access: port 5100. After restart: port 5200", the
//      note says a restart is needed and names both addresses after it
//   2. both pending: ONE Restart now on the page, and its text names both
//   3. Apply posts the port, then asks "Restart now / Later"; Later posts no
//      restart, Restart now posts /api/hub/restart
//   4. a box without the name helper says "needs a one-time update by the
//      installer" with no form and no command
//   5. a box that cannot restart itself offers no button and says to switch
//      it off and on
//   6. the last restart's refusal is said in plain words (kept the old value)
//   7. no sudo, systemctl, sed or em dash anywhere on either card
//   8. the session token is handed only to the box's ADDRESS, never to a
//      .local name the box may not own yet (Pipi L1); with no address the
//      name is opened bare
//   9. a hub that is not the box's own service (restart.applies false): both
//      Apply buttons disabled, the cards say so in plain words, and a saved
//      change is shown without any promise that it applies at a restart
//  10. the same card is ONE choice of two (Tok Aba, 2026-10-07): "Use proxy
//      (port 80)" hides the port (80 is fixed), Test and Apply post the
//      mode, the restart prompt names the address with no port; a pending
//      or live proxy mode reads as such; a server without it draws no choice
//  12. customer view (Tok Aba, 2026-10-14; user.is_developer false): Dashboard
//      Access as "Access method" radio rows (Standard Access / Custom Port,
//      the port field only under Custom Port), "Current dashboard address"
//      with a link and Copy, the restart note, Test connection and Apply
//      settings; Device Name as the "Device hostname" field with its hint,
//      "Your device address" with a copy button, the note and Save Device
//      Name. The live/pending summaries, the longer text and the factory-name
//      suggestion are drawn for a developer account only. Cases 1-11 run as a
//      developer account.
//
//   node tests/check_boxrestart.mjs
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".boxrestart-test.cjs");

const dom = new JSDOM("<!doctype html><html><body></body></html>",
                      { pretendToBeVisual: true, url: "http://192.168.1.21:5100/" });
global.window = dom.window;
global.document = dom.window.document;
Object.defineProperty(global, "navigator",
                      { value: dom.window.navigator, configurable: true });
for (const k of ["HTMLElement", "HTMLInputElement", "Element", "Node", "SVGElement",
                 "Event", "CustomEvent", "MouseEvent", "requestAnimationFrame",
                 "cancelAnimationFrame", "getComputedStyle", "localStorage"]) {
  global[k] = dom.window[k];
}
global.IS_REACT_ACT_ENVIRONMENT = true;
global.sessionStorage = dom.window.sessionStorage;
dom.window.sessionStorage.setItem("fx-session", "tok123");

let bad = 0;
const check = (msg, passed, detail = "") => {
  if (passed) console.log(`ok   ${msg}`);
  else { console.error(`FAIL ${msg}${detail ? `\n      ${detail}` : ""}`); bad += 1; }
};

// GET answers globalThis.__get[url]; every POST is recorded and answers
// globalThis.__post[url] (or the GET status for that url).
const shimApi = {
  name: "shim-api",
  setup(b) {
    b.onResolve({ filter: /api\/client\.js$/ }, () => ({ path: "api-shim", namespace: "shim" }));
    b.onLoad({ filter: /^api-shim$/, namespace: "shim" }, () => ({
      contents: `
        globalThis.__posts = [];
        export const api = {
          post(url, body) { globalThis.__posts.push([url, body]);
            return Promise.resolve(globalThis.__post[url] || globalThis.__get[url]); },
          get(url) { return Promise.resolve(globalThis.__get[url]); },
        };
        export function friendlyError(e) { return String(e && e.message || e); }
      `,
      resolveDir: FRONT, loader: "js",
    }));
  },
};
const shimAsk = {
  name: "shim-ask",
  setup(b) {
    b.onResolve({ filter: /ui\/ask\.jsx$/ }, () => ({ path: "ask-shim", namespace: "shim" }));
    b.onLoad({ filter: /^ask-shim$/, namespace: "shim" }, () => ({
      contents: `
        globalThis.__asked = [];
        globalThis.__answers = [];
        const confirm = async (props) => {
          globalThis.__asked.push(props);
          return globalThis.__answers.length ? globalThis.__answers.shift() : false;
        };
        export function useConfirm() { return confirm; }
      `,
      resolveDir: FRONT, loader: "js",
    }));
  },
};

// The cards read the account (useIsDeveloper), switched per case here.
globalThis.__user = { is_developer: true };
const shimApp = {
  name: "shim-app",
  setup(b) {
    b.onResolve({ filter: /state\/app\.jsx$/ }, () => ({ path: "app-shim", namespace: "shim" }));
    b.onLoad({ filter: /^app-shim$/, namespace: "shim" }, () => ({
      contents: `export function useApp() { return { user: globalThis.__user }; }
        export function useIsDeveloper() { return !!globalThis.__user?.is_developer; }`,
      resolveDir: FRONT, loader: "js",
    }));
  },
};

const p = (rel) => join(FRONT, rel).replace(/\\/g, "/");
await build({
  plugins: [shimApi, shimAsk, shimApp],
  stdin: {
    contents: `
      import { createRoot } from "react-dom/client";
      import { act, createElement as h, Fragment } from "react";
      import SoftwarePortCard from "${p("src/components/SoftwarePortCard.jsx")}";
      import HostnameCard from "${p("src/components/HostnameCard.jsx")}";
      export { handoff } from "${p("src/components/BoxRestartNote.jsx")}";
      export { sessionCarriesTo } from "${p("src/lib/session.js")}";
      export { act };
      export function mount(el) {
        const root = createRoot(el);
        act(() => { root.render(h(Fragment, null, h(SoftwarePortCard), h(HostnameCard))); });
        return { unmount: () => act(() => root.unmount()) };
      }
    `,
    resolveDir: FRONT, loader: "js",
  },
  bundle: true, format: "cjs", platform: "node", outfile: OUT,
  jsx: "automatic", logLevel: "silent", loader: { ".css": "empty" },
  alias: { "@": join(FRONT, "src") },
  define: { "process.env.NODE_ENV": '"development"' },
});
const m = req(OUT);
const { act } = m;

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
const buttons = (el, text) => [...el.querySelectorAll("button")]
  .filter((b) => b.textContent.trim() === text);
// A radio row: its bold label is the name (the line under it is not).
const radio = (el, label) => [...el.querySelectorAll("[role=radio]")]
  .find((b) => b.querySelector("[data-testid=access-label]")?.textContent === label);
const cardOf = (box, title) => [...box.querySelectorAll(".panel-card")]
  .find((c) => c.querySelector("strong")?.textContent === title);

const after = (port, name) => ({ port, name: `${name}.local`,
  name_url: `http://${name}.local:${port}`, ip_urls: [`http://192.168.1.21:${port}`] });
function statuses({ port = null, name = null, canReboot = true, canSet = true, last = null,
                    applies = true }) {
  const restart = { port, hostname: name, any: !!(port || name), can_reboot: canReboot,
                    applies, current: { port: 5100, name: "iriv.local" },
                    after: after(port || 5100, name || "iriv"), last };
  return {
    "/api/hub/port": { ok: true, port: 5100, running: 5100, pending: port, default: 5100,
                       source: "config", systems: [{ slug: "demo", port: 5110 }],
                       url: "http://192.168.1.21:5100", restart },
    "/api/hub/hostname": { ok: true, hostname: "iriv", local_name: "iriv.local",
                           default: "iriv-b2ca6353", shared_name: true, is_default: false,
                           can_set: canSet, helper_installed: canSet, via_name: false,
                           port: 5100, pending: name, restart },
  };
}

async function render(get, fn, post = {}) {
  globalThis.__get = get;
  globalThis.__post = post;
  globalThis.__posts.length = 0;
  globalThis.__asked.length = 0;
  globalThis.__answers.length = 0;
  const box = document.createElement("div");
  document.body.appendChild(box);
  const r = m.mount(box);
  await flush();
  try { return await fn(box); } finally { r.unmount(); document.body.removeChild(box); }
}

const FORBIDDEN = /sudo|systemctl|sed -i|daemon-reload|\u2014/;

// 1. a pending port
await render(statuses({ port: 5200 }), async (box) => {
  const port = cardOf(box, "Dashboard Access");
  const now = port.querySelector("[data-testid=port-now]").textContent;
  check("the port card shows the current and the pending value",
        now.includes("Access: port 5100.") && now.includes("After restart: port 5200"), now);
  const text = port.textContent;
  check("...says a restart is needed and names both addresses after it",
        text.includes("A restart is needed")
        && text.includes("After the restart, open http://iriv.local:5200 or http://192.168.1.21:5200"),
        text);
  check("...offers Restart now and a way to keep the current port",
        buttons(box, "Restart now").length === 1 && buttons(port, "Keep port 5100").length === 1);
  check("no command on the page (sudo, systemctl, sed) and no em dash",
        !FORBIDDEN.test(box.textContent), box.textContent.match(FORBIDDEN)?.[0]);
});

// 2. both pending: one note, one button
await render(statuses({ port: 5200, name: "gate-a" }), async (box) => {
  const name = cardOf(box, "Device Name");
  check("the name card shows the current and the pending name",
        name.querySelector("[data-testid=name-now]").textContent
          .includes("After restart: gate-a.local"));
  check("both pending: ONE Restart now on the page", buttons(box, "Restart now").length === 1);
  const note = cardOf(box, "Dashboard Access").textContent;
  check("...whose note names both changes and the one address after",
        note.includes("Port: 5100 now, 5200 after the restart.")
        && note.includes("Device name: iriv.local now, gate-a.local after the restart.")
        && note.includes("http://gate-a.local:5200"), note);
});

// 2b. only the name pending: the note moves to the name card
await render(statuses({ name: "gate-a" }), async (box) => {
  check("only a name pending: the name card carries the one Restart now",
        buttons(cardOf(box, "Device Name"), "Restart now").length === 1
        && buttons(cardOf(box, "Dashboard Access"), "Restart now").length === 0);
});

// 3. Apply, then Later / Restart now
const saved = statuses({ port: 5300 });
await render(statuses({}), async (box) => {
  const port = cardOf(box, "Dashboard Access");
  await type(port.querySelector("input"), "5300");
  await click(buttons(port, "Apply settings")[0]);
  const ask = globalThis.__asked[0];
  check("Apply posts the port", globalThis.__posts[0]?.[0] === "/api/hub/port"
        && globalThis.__posts[0][1].port === 5300, JSON.stringify(globalThis.__posts));
  check("...then asks Restart now / Later, naming the address after it",
        ask && ask.confirmLabel === "Restart now" && ask.cancelLabel === "Later"
        && ask.description.includes("http://iriv.local:5300")
        && ask.description.includes("keeps the current port and hostname until it restarts")
        && !FORBIDDEN.test(ask.description), JSON.stringify(ask));
  check("Later posts no restart",
        !globalThis.__posts.some(([u]) => u === "/api/hub/restart"));
}, { "/api/hub/port": saved["/api/hub/port"] });

await render(statuses({}), async (box) => {
  const port = cardOf(box, "Dashboard Access");
  await type(port.querySelector("input"), "5300");
  globalThis.__answers.push(true);
  await click(buttons(port, "Apply settings")[0]);
  check("Restart now posts /api/hub/restart",
        globalThis.__posts.some(([u]) => u === "/api/hub/restart"));
  check("...and the card says the box is restarting and where to go",
        port.textContent.includes("The box is restarting")
        && port.textContent.includes("http://192.168.1.21:5300"), port.textContent);
}, { "/api/hub/port": saved["/api/hub/port"],
     "/api/hub/restart": { ok: true, restarting: true, after: after(5300, "iriv") } });

// 4. no helper
await render(statuses({ canSet: false }), async (box) => {
  const name = cardOf(box, "Device Name");
  check("no helper: plain words, no form",
        name.querySelector("[data-testid=needs-installer]")?.textContent
          .includes("This device needs a one-time update by the installer")
        && !name.querySelector("input") && !FORBIDDEN.test(name.textContent),
        name.textContent);
});

// 5. cannot restart itself
await render(statuses({ port: 5200, canReboot: false }), async (box) => {
  check("no reboot rule: no Restart now, told to switch it off and on",
        buttons(box, "Restart now").length === 0
        && box.textContent.includes("switch it off and on again"));
});

// 6. the last restart refused a change
await render(statuses({ last: { boot: "b", port_ok: false, port_from: "5100", port_to: "5300",
                                port_detail: "something on this box was already using 5300" } }),
async (box) => {
  const text = cardOf(box, "Dashboard Access").textContent;
  check("a refused change at the restart is said in plain words",
        text.includes("The box restarted but kept the port 5100, because something on "
                      + "this box was already using 5300."), text);
});

// 8. where the session token may go
{
  const h = m.handoff(after(5300, "gate-a"));
  check("the token goes to the box's address only",
        h.go === "http://192.168.1.21:5300" && h.poll === "http://192.168.1.21:5300"
        && m.sessionCarriesTo(h.go), JSON.stringify(h));
  check("...and it is never IN the address",
        !h.go.includes("session") && !h.go.includes("tok123"), h.go);
  check("...never to the new .local name",
        !h.go.includes(".local") && !m.sessionCarriesTo("http://gate-a.local:5300"), h.go);
  const bare = m.handoff({ name_url: "http://gate-a.local:5300", ip_urls: [] });
  check("no address known: the name is opened WITHOUT the token",
        bare.go === "http://gate-a.local:5300" && !m.sessionCarriesTo(bare.go), JSON.stringify(bare));
}

// 10. the two choices on the one card (Tok Aba, 2026-10-07): "Use proxy
//     (port 80)" or "Custom port"; same Test / Apply / restart prompt
function withAccess({ mode = "port", pending = null, live = null, port = null } = {}) {
  const s = statuses({ port });
  const proxyAfter = (pending || mode) === "proxy";
  const restart = { ...s["/api/hub/port"].restart, access: pending,
                    any: !!(port || pending),
                    current: { port: 5100, name: "iriv.local", access: mode },
                    after: proxyAfter ? { port: 80, name: "iriv.local", proxy: true,
                                          name_url: "http://iriv.local",
                                          ip_urls: ["http://192.168.1.21"] }
                      : after(port || 5100, "iriv") };
  s["/api/hub/port"] = { ...s["/api/hub/port"], restart,
                         access: { mode, pending, live, modes: ["port", "proxy"],
                                   default: "port", prefix: "/s/" } };
  s["/api/hub/hostname"] = { ...s["/api/hub/hostname"], restart };
  return s;
}
const checkedOf = (el) => [...el.querySelectorAll("[role=radio]")]
  .filter((b) => b.getAttribute("aria-checked") === "true")
  .map((b) => b.querySelector("[data-testid=access-label]")?.textContent);

await render(withAccess(), async (box) => {
  const card = cardOf(box, "Dashboard Access");
  check("both choices are offered, Custom port chosen in port mode",
        !!radio(card, "Standard Access")
        && JSON.stringify(checkedOf(card)) === '["Custom Port"]', checkedOf(card));
  await click(radio(card, "Standard Access"));
  check("Use proxy: no port to type (80 is fixed), and the address shape is said",
        !card.querySelector("input")
        && card.querySelector("[data-testid=proxy-explain]")?.textContent.includes("/s/")
        && card.textContent.includes("Port 80 is fixed"), card.textContent);
  await click(buttons(card, "Test connection")[0]);
  check("Test asks about port 80, not a port",
        JSON.stringify(globalThis.__posts.at(-1)) === '["/api/hub/port/test",{"mode":"proxy"}]',
        JSON.stringify(globalThis.__posts));
  await click(buttons(card, "Apply settings")[0]);
  const ask = globalThis.__asked[0];
  check("Apply saves the proxy choice",
        JSON.stringify(globalThis.__posts.at(-1)) === '["/api/hub/port",{"mode":"proxy"}]',
        JSON.stringify(globalThis.__posts));
  check("...and the restart prompt names the address with no port",
        ask && ask.description.includes("open http://iriv.local or http://192.168.1.21.")
        && ask.description.includes("Address: a custom port now, Standard Access (port 80)")
        && !FORBIDDEN.test(ask.description), ask && ask.description);
}, { "/api/hub/port/test": { ok: true, usable: true, detail: "ready - the box can answer on port 80",
                             checks: [] },
     "/api/hub/port": withAccess({ pending: "proxy" })["/api/hub/port"] });

await render(withAccess({ pending: "proxy" }), async (box) => {
  const card = cardOf(box, "Dashboard Access");
  check("a pending proxy: shown both ways, the note sessionCarriesTo it, one Restart now",
        card.querySelector("[data-testid=port-now]").textContent
          .includes("After restart: Standard Access (port 80)")
        && card.textContent.includes("A restart is needed")
        && buttons(box, "Restart now").length === 1, card.textContent);
  await click(buttons(card, "Keep port 5100")[0]);
  check("Keep port 5100 cancels it with the custom port the box has",
        JSON.stringify(globalThis.__posts.at(-1))
          === '["/api/hub/port",{"mode":"port","port":5100}]', JSON.stringify(globalThis.__posts));
});

await render(withAccess({ mode: "proxy", live: false }), async (box) => {
  const card = cardOf(box, "Dashboard Access");
  check("proxy mode: answering on port 80, Use proxy chosen",
        card.querySelector("[data-testid=port-now]").textContent.includes("Standard Access (port 80)")
        && JSON.stringify(checkedOf(card)) === '["Standard Access"]', card.textContent);
  check("...port 80 not answering is said, links keep their port",
        card.querySelector("[data-testid=proxy-not-answering]")?.textContent
          .includes("links keep their port"));
  check("...Apply is off while the choice is what the box has",
        buttons(card, "Apply settings")[0].disabled);
  await click(radio(card, "Custom Port"));
  await click(buttons(card, "Apply settings")[0]);
  check("back to Custom port posts the port mode with the port",
        JSON.stringify(globalThis.__posts.at(-1))
          === '["/api/hub/port",{"mode":"port","port":5100}]', JSON.stringify(globalThis.__posts));
}, { "/api/hub/port": withAccess({ mode: "proxy", pending: "port" })["/api/hub/port"] });

await render(statuses({}), async (box) => {
  check("a server without the setting: no choice drawn, the port card as before",
        cardOf(box, "Dashboard Access").querySelectorAll("[role=radio]").length === 0
        && !!cardOf(box, "Dashboard Access").querySelector("input"));
});

// 11. The 2026-10-11 copy, as a developer account still reads it: the longer
//     text in the developer block, worded for the selected choice, and no
//     "proxy" anywhere on the page.
await render(withAccess(), async (box) => {
  const card = cardOf(box, "Dashboard Access");
  const dev = card.querySelector("[data-testid=access-dev-extras]");
  check("developer: the longer text for Custom Port",
        dev?.textContent.replace(/\s+/g, " ").includes(
          "Existing bookmarks that include the old port number need to be updated."));
  await click(radio(card, "Standard Access"));
  check("...and for Standard Access, with the address shape",
        card.querySelector("[data-testid=proxy-explain]")?.textContent.includes("/s/"));
  check("...and the word proxy is nowhere the customer reads",
        !/proxy/i.test(box.textContent), box.textContent.match(/.{30}proxy.{30}/i)?.[0]);
  const name = cardOf(box, "Device Name");
  check("developer: Device Name keeps the longer text and the factory-name suggestion",
        name.querySelector("[data-testid=name-dev-extras]")?.textContent.includes(
          "Choose a unique name for this device on your local network.")
        && buttons(name, "Use iriv-b2ca6353").length === 1);
});
await render((() => { const s = statuses({});
  s["/api/hub/hostname"] = { ...s["/api/hub/hostname"], local_name: "iriv-2.local",
                             shared_name: false };
  return s; })(), async (box) => {
  check("a name another device holds: Device Name Already in Use",
        cardOf(box, "Device Name").querySelector("[data-testid=name-in-use]")?.textContent
          .replace(/\s+/g, " ").includes("Device Name Already in Use. Another device on the network is using iriv"));
});

// 12. customer view (Tok Aba, 2026-10-14) -----------------------------------
globalThis.__user = { is_developer: false };
await render(withAccess(), async (box) => {
  const card = cardOf(box, "Dashboard Access");
  const t = card.textContent.replace(/\s+/g, " ");
  check("customer: Dashboard Access title and subtitle",
        card.querySelector("[data-testid=access-about]")?.textContent.trim()
          === "Choose how to access your device dashboard.");
  const rows = [...card.querySelectorAll("[role=radiogroup] [role=radio]")];
  check("...Access method: two radio rows with their lines",
        t.includes("Access method") && rows.length === 2
        && rows[0].textContent.replace(/\s+/g, " ").includes(
          "Standard Access" + "Use the default web address without specifying a port.")
        && rows[1].textContent.replace(/\s+/g, " ").includes(
          "Custom Port" + "Use a specific port number. Intended for advanced configurations."),
        rows.map((r) => r.textContent).join(" | "));
  check("...the port field under Custom Port, chosen in port mode",
        JSON.stringify(checkedOf(card)) === '["Custom Port"]'
        && !!card.querySelector("[data-testid=access-port-field] input"));
  const addr = card.querySelector("[data-testid=access-address]");
  check("...Current dashboard address: a link that opens it, and Copy",
        addr?.textContent.includes("Current dashboard address")
        && card.querySelector("[data-testid=access-url]")?.getAttribute("href") === "http://192.168.1.21:5100"
        && card.querySelector("[data-testid=access-url]")?.getAttribute("target") === "_blank"
        && buttons(card, "Copy").length === 1);
  check("...the restart note",
        card.querySelector("[data-testid=access-note]")?.textContent.replace(/\s+/g, " ").includes(
          "Changing the access method may require a device restart. Your dashboard may be "
          + "temporarily unavailable while the change takes effect."));
  check("...Test connection and Apply settings, side by side",
        buttons(card, "Test connection").length === 1 && buttons(card, "Apply settings").length === 1
        && buttons(card, "Test connection")[0].parentElement === buttons(card, "Apply settings")[0].parentElement);
  check("...none of the developer extras",
        !card.querySelector("[data-testid=access-dev-extras]") && !card.querySelector("[data-testid=port-now]")
        && !t.includes("Existing bookmarks") && !t.includes("5110"), t);
  Object.defineProperty(globalThis.navigator, "clipboard", { configurable: true,
    value: { writeText: async (text) => { globalThis.__copied = text; } } });
  await click(buttons(card, "Copy")[0]);
  check("...Copy copies the address", globalThis.__copied === "http://192.168.1.21:5100",
        String(globalThis.__copied));
  await click(radio(card, "Standard Access"));
  check("...Standard Access hides the port field",
        JSON.stringify(checkedOf(card)) === '["Standard Access"]'
        && !card.querySelector("[data-testid=access-port-field]"));
  await click(buttons(card, "Test connection")[0]);
  check("...Test connection runs the device's check of the chosen method",
        JSON.stringify(globalThis.__posts.at(-1)) === '["/api/hub/port/test",{"mode":"proxy"}]'
        && card.querySelector("[data-testid=port-tested]")?.textContent.includes("ready"),
        JSON.stringify(globalThis.__posts));
  await click(buttons(card, "Apply settings")[0]);
  check("...Apply settings saves it and asks Restart now / Later",
        JSON.stringify(globalThis.__posts.at(-1)) === '["/api/hub/port",{"mode":"proxy"}]'
        && globalThis.__asked[0]?.confirmLabel === "Restart now");

  const name = cardOf(box, "Device Name");
  const n = name.textContent.replace(/\s+/g, " ");
  check("customer: Device Name title and subtitle",
        name.querySelector("[data-testid=name-about]")?.textContent.trim()
          === "Give your device a name that's easy to recognize on your network.");
  check("...Device hostname, its field and the hint",
        n.includes("Device hostname") && name.querySelector("input")?.value === "iriv"
        && name.querySelector("[data-testid=name-hint]")?.textContent.trim()
          === "Use letters, numbers, and hyphens. Avoid spaces.");
  check("...Your device address with a copy button",
        name.querySelector("[data-testid=name-address]")?.textContent.includes("Your device address")
        && name.querySelector("[data-testid=name-local]")?.textContent === "iriv.local"
        && !!name.querySelector("[data-testid=name-copy]"));
  check("...the restart note and Save Device Name",
        name.querySelector("[data-testid=name-note]")?.textContent.replace(/\s+/g, " ").includes(
          "The new name takes effect after the device restarts. Your device may be temporarily "
          + "unavailable during this process.")
        && buttons(name, "Save Device Name").length === 1);
  check("...none of the developer extras (summary, longer text, factory-name suggestion)",
        !name.querySelector("[data-testid=name-dev-extras]") && !name.querySelector("[data-testid=name-now]")
        && !n.includes("factory name") && buttons(name, "Use iriv-b2ca6353").length === 0, n);
  await click(name.querySelector("[data-testid=name-copy]"));
  check("...the copy button copies the address", globalThis.__copied === "iriv.local");
  await type(name.querySelector("input"), "gate-a");
  await click(buttons(name, "Save Device Name")[0]);
  check("...Save Device Name posts the name",
        JSON.stringify(globalThis.__posts.at(-1)) === '["/api/hub/hostname",{"name":"gate-a"}]',
        JSON.stringify(globalThis.__posts));
  check("...no em dash, no command", !FORBIDDEN.test(box.textContent));
}, { "/api/hub/port/test": { ok: true, usable: true, detail: "ready - the box can answer on port 80",
                             checks: [] },
     "/api/hub/port": withAccess({ pending: "proxy" })["/api/hub/port"] });
await render(statuses({ port: 5200 }), async (box) => {
  const card = cardOf(box, "Dashboard Access");
  check("customer: a pending change still shows the restart note and Keep",
        card.textContent.includes("A restart is needed") && buttons(card, "Keep port 5100").length === 1
        && buttons(box, "Restart now").length === 1);
});
await render((() => { const s = statuses({});
  s["/api/hub/hostname"] = { ...s["/api/hub/hostname"], local_name: "iriv-2.local",
                             shared_name: false };
  return s; })(), async (box) => {
  check("customer: Device Name Already in Use is still said",
        !!cardOf(box, "Device Name").querySelector("[data-testid=name-in-use]"));
});
globalThis.__user = { is_developer: true };

if (bad) {
  console.error(`\n${bad} check(s) failed`);
  process.exit(1);
}
console.log("\nall good");
process.exit(0);
