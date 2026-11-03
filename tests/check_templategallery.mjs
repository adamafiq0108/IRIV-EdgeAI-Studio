// Template first: the Hub home's "Start from a template" cards, their
// preview, and the create wizard they open (Tok Aba, 2026-10-22, meeting
// notes Entries 1 and 2).
//
// Pinned by rendering TemplateGallery and CreateSystemWizard the way
// HubShell joins them (a card's "Use this template" opens the wizard with
// that key) against a shimmed api:
//   1. the cards render, Blank Canvas first; a template with a picture shows
//      it, one without draws the placeholder
//   2. a card opens the preview: title, its line, a "What you get" list read
//      from the template's build, a slider when it has several pictures
//   3. "Use this template" lands on the Name step (step 1 of 2) with that
//      template named; the wizard has no "What is it detecting?" step
//   4. "Change template" reopens the cards in a dialog; choosing another
//      keeps the name typed
//   5. Back on the Name step leaves the wizard (onCancel)
//   6. Create posts the chosen template's key
//   7. a non-admin sees no cards at all
//   8. the Hub and HubShell wire it that way; no em dash
//   9. the Hub home (Tok Aba, after .164): templates ABOVE "Your systems",
//      always shown (also selecting); the grid shows 4 places, a system
//      takes one, the rest are New system tiles (4, 3, 1, 0); at the cap
//      a plain notice, Use this template and New System disabled
//  10. "Check for new templates" runs the manual, unlimited check (POST
//      /api/hub/templates/update), says what came in -- preview pictures
//      too -- and redraws the cards
//
//   node tests/check_templategallery.mjs
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".templategallery-test.cjs");

const dom = new JSDOM("<!doctype html><html><body></body></html>",
                      { pretendToBeVisual: true, url: "http://192.168.1.21:5100/" });
global.window = dom.window;
global.document = dom.window.document;
Object.defineProperty(global, "navigator",
                      { value: dom.window.navigator, configurable: true });
for (const k of ["HTMLElement", "HTMLInputElement", "Element", "Node", "SVGElement",
                 "Event", "CustomEvent", "MouseEvent", "KeyboardEvent", "FocusEvent",
                 "requestAnimationFrame", "cancelAnimationFrame", "getComputedStyle",
                 "localStorage", "DocumentFragment"]) {
  if (dom.window[k]) global[k] = dom.window[k];
}
global.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
global.IS_REACT_ACT_ENVIRONMENT = true;

let bad = 0;
const check = (msg, passed, detail = "") => {
  if (passed) console.log(`ok   ${msg}`);
  else { console.error(`FAIL ${msg}${detail ? `\n      ${detail}` : ""}`); bad += 1; }
};

const shim = (name, filter, contents) => ({
  name,
  setup(b) {
    b.onResolve({ filter }, (a) => (
      name !== "api-shim" || a.path.endsWith("api/client.js")
      || (a.path === "./client.js" && /[\\/]src[\\/]api$/.test(a.resolveDir))
        ? { path: name, namespace: "shim" } : undefined));
    b.onLoad({ filter: new RegExp(`^${name}$`), namespace: "shim" }, () => ({
      contents, resolveDir: FRONT, loader: "js" }));
  },
});
const shims = [
  shim("api-shim", /client\.js$/, `
    globalThis.__sent = [];
    export const api = {
      get(url) { (globalThis.__gets ||= []).push(url); return Promise.resolve(globalThis.__get(url)); },
      post(url, body) {
        globalThis.__sent.push(["POST", url, body]);
        if (url === "/api/hub/templates/update") {
          return globalThis.__update instanceof Error
            ? Promise.reject(globalThis.__update) : Promise.resolve(globalThis.__update);
        }
        if (url === "/api/hub/templates/refresh") return Promise.resolve({ ok: true, state: "current" });
        return Promise.resolve({ ok: true, name: body.name, started: true, url: "http://x", warnings: [] });
      },
    };
    export function friendlyError(e) { return String(e && e.message || e); }`),
  shim("app-shim", /state\/app\.jsx$/, `
    export function useApp() { return { user: globalThis.__user }; }`),
  shim("toast-shim", /ui\/Toast\.jsx$/, `
    export function useToast() { const n = () => {}; return { success: n, error: n, info: n, warn: n }; }`),
  shim("ask-shim", /ui\/ask\.jsx$/, `
    export function useConfirm() { return async () => true; }`),
];

const p = (rel) => join(FRONT, rel).replace(/\\/g, "/");
await build({
  plugins: shims,
  stdin: {
    contents: `
      import { createRoot } from "react-dom/client";
      import { act, createElement as h, useState } from "react";
      import TemplateGallery from "${p("src/components/TemplateGallery.jsx")}";
      import CreateSystemWizard from "${p("src/components/CreateSystemWizard.jsx")}";
      import Hub from "${p("src/pages/Hub.jsx")}";
      export { act };
      export function mountHub(el) {
        const root = createRoot(el);
        globalThis.__creates = [];
        act(() => { root.render(h(Hub, { onCreate: (f, k) => globalThis.__creates.push([f, k]) })); });
        return { unmount: () => act(() => root.unmount()) };
      }
      // HubShell's join, in miniature: the Hub's cards until one is used,
      // then the wizard with that template; Back returns to the cards.
      function Flow() {
        const [tpl, setTpl] = useState(null);
        globalThis.__wizardOpen = tpl !== null;
        return tpl === null
          ? h(TemplateGallery, { onUse: (key) => setTpl(key) })
          : h(CreateSystemWizard, { template: tpl, onCancel: () => setTpl(null) });
      }
      export function mount(el) {
        const root = createRoot(el);
        act(() => { root.render(h(Flow)); });
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

const wait = async (ms) => {
  await act(async () => { await new Promise((r) => setTimeout(r, ms)); });
  for (let i = 0; i < 4; i += 1) {
    // eslint-disable-next-line no-await-in-loop
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};
const click = async (el) => {
  await act(async () => { el.dispatchEvent(new window.MouseEvent("click", { bubbles: true })); });
  await wait(0);
};
const type = async (el, value) => {
  const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
  await act(async () => {
    setter.call(el, value);
    el.dispatchEvent(new window.Event("input", { bubbles: true }));
  });
};
const button = (text) => [...document.querySelectorAll("button")]
  .find((b) => b.textContent.trim() === text);
const cards = (root = document) => [...root.querySelectorAll("[data-template-card]")];
const card = (key, root = document) => root.querySelector(`[data-template-card="${key}"]`);
const body = () => document.body.textContent;

globalThis.__user = { is_admin: true };
globalThis.__get = (url) => {
  if (url === "/api/hub/port") return { ok: true, running: 5100 };
  if (url.startsWith("/api/hub/ports/check")) return { ok: true, suggested: 5113 };
  if (url === "/api/hub/templates") {
    return { ok: true, default: "blank_canvas", saved: [], updates: { token: true }, templates: [
      { key: "blank_canvas", label: "Blank Canvas", hint: "Start from nothing.", classes: ["person"],
        images: [], preview: { pages: ["Dashboard"], cards: [], steps: 1, tables: [] } },
      { key: "counting", label: "People Counting", hint: "Count people crossing a line.",
        classes: ["person"], images: ["/static/img/templates/counting.png",
                                      "/static/img/templates/counting-2.png"],
        preview: { pages: ["Dashboard", "Overview"], cards: ["In", "Out", "Inside"],
                   steps: 26, tables: ["Counting"] } },
      { key: "restricted_zone", label: "Restricted Zone", hint: "Alert when someone enters.",
        classes: ["person"], images: [],
        preview: { pages: ["Dashboard"], cards: ["Restricted Zone"], steps: 7, tables: [] } },
    ] };
  }
  return { ok: true };
};

const host = document.createElement("div");
document.body.appendChild(host);
let ui = m.mount(host);
await wait(0);

// ---- 1. the cards ---------------------------------------------------------------
check("the cards render, one per template",
      cards(host).length === 3 && !!host.querySelector("[data-template-gallery]"));
check("...under a plain heading", host.textContent.includes("Start from a template"));
check("...Blank Canvas first", cards(host)[0]?.dataset.templateCard === "blank_canvas",
      cards(host).map((c) => c.dataset.templateCard).join());
check("...each with its title and one line",
      card("counting").textContent.includes("People Counting")
      && card("counting").textContent.includes("Count people crossing a line."));
check("a template with a picture shows it",
      card("counting").querySelector("[data-template-art=image] img")?.getAttribute("src")
        === "/static/img/templates/counting.png");
check("...one without draws the placeholder",
      !!card("restricted_zone").querySelector("[data-template-art=placeholder] svg"));

// ---- 2. the preview -------------------------------------------------------------
await click(card("counting"));
const pv = () => document.querySelector("[data-template-preview]");
check("a card opens the preview", pv()?.dataset.templatePreview === "counting");
check("...titled, with its line",
      pv().textContent.includes("People Counting") && pv().textContent.includes("Count people crossing a line."));
const got = pv().querySelector("[data-what-you-get]")?.textContent || "";
check("...and What you get, read from the template",
      got.includes("Looks for: people") && got.includes("In, Out, Inside")
      && got.includes("(2 pages)") && got.includes("26 blocks") && got.includes("Counting"), got);
const pic = () => pv().querySelector("img")?.getAttribute("src");
check("...a slider over its pictures", pic() === "/static/img/templates/counting.png"
      && !!pv().querySelector('[aria-label="Next picture"]'));
await click(pv().querySelector('[aria-label="Next picture"]'));
check("...Next shows the second", pic() === "/static/img/templates/counting-2.png", pic());
check("the wizard is not open yet", globalThis.__wizardOpen === false);

// ---- 3. Use this template -------------------------------------------------------
await click(button("Use this template"));
check("Use this template opens the wizard and closes the preview",
      globalThis.__wizardOpen === true && !pv());
check("...on the Name step, step 1 of 2",
      host.textContent.includes("Step 1 of 2") && !!host.querySelector('[aria-label="System name"]'));
check("...with two steps listed, no detecting question",
      host.querySelectorAll("ol li").length === 2 && !host.textContent.includes("What is it detecting"));
check("...and the template named",
      host.querySelector("[data-chosen-template]")?.dataset.chosenTemplate === "counting"
      && host.querySelector("[data-chosen-template]").textContent.includes("People Counting"));

// ---- 4. Change template ---------------------------------------------------------
await type(host.querySelector('[aria-label="System name"]'), "Front Door");
await wait(0);
await click(button("Change template"));
const dlg = () => document.querySelector("[role=dialog]");
check("Change template reopens the cards in a dialog", cards(dlg()).length === 3);
await click(card("restricted_zone", dlg()));
check("...a card there opens its preview in place",
      pv()?.dataset.templatePreview === "restricted_zone" && !!button("All templates"));
await click(button("Use this template"));
check("...choosing it closes the dialog and changes the template",
      !dlg() && host.querySelector("[data-chosen-template]")?.dataset.chosenTemplate === "restricted_zone");
check("...and the name typed is kept",
      host.querySelector('[aria-label="System name"]')?.value === "Front Door");

// ---- 6. Create posts the key ----------------------------------------------------
await click(button("Next"));
check("Review names the template", host.textContent.includes("Restricted Zone")
      && host.textContent.includes("Step 2 of 2"));
globalThis.__sent.length = 0;
await click(button("Create system"));
const creates = globalThis.__sent.filter(([, u]) => u === "/api/hub/systems");
check("Create posts the chosen template's key",
      creates.length === 1 && creates[0][2].template === "restricted_zone"
      && creates[0][2].name === "Front Door", JSON.stringify(globalThis.__sent));
check("no em dash anywhere", !body().includes("\u2014"));
ui.unmount();

// ---- 5. Back leaves -------------------------------------------------------------
ui = m.mount(host);
await wait(0);
await click(card("blank_canvas"));
check("Blank Canvas says what you get in plain words",
      (pv()?.querySelector("[data-what-you-get]")?.textContent || "").includes("An empty dashboard"));
await click(button("Use this template"));
check("Blank Canvas opens the wizard too", globalThis.__wizardOpen === true);
await click(button("Back"));
check("Back on the Name step leaves the wizard", globalThis.__wizardOpen === false
      && cards(host).length === 3);
ui.unmount();

// ---- 7. non-admin ---------------------------------------------------------------
globalThis.__user = { is_admin: false };
ui = m.mount(host);
await wait(0);
check("a non-admin sees no cards", !host.querySelector("[data-template-gallery]")
      && cards(host).length === 0);
ui.unmount();
host.remove();

// ---- 8. the wiring --------------------------------------------------------------
const hub = readFileSync(join(FRONT, "src", "pages", "Hub.jsx"), "utf8");
const shell = readFileSync(join(FRONT, "src", "components", "HubShell.jsx"), "utf8");
check("the Hub's cards open the wizard with their key",
      hub.includes("onUse={(key) => onCreate(undefined, key)}") && hub.includes("<TemplateGallery"));
check("...and HubShell hands it over",
      shell.includes("setPendingTemplate(template || \"\")") && shell.includes("template={pendingTemplate}"));
const wiz = readFileSync(join(FRONT, "src", "components", "CreateSystemWizard.jsx"), "utf8");
check("the wizard's steps are name and review", wiz.includes('const STEPS = ["name", "review"];'));

// ---- 10. the manual check ---------------------------------------------------------
globalThis.__user = { is_admin: true };
const chkHost = document.createElement("div");
document.body.appendChild(chkHost);
ui = m.mount(chkHost);
await wait(0);
const chk = () => chkHost.querySelector("[data-template-check]");
const result = () => chkHost.querySelector("[data-template-check-result]")?.textContent || "";
check("the cards carry a quiet Check for new templates, beside the up to date line",
      chk()?.textContent === "Check for new templates"
      && chkHost.querySelector("[data-template-refresh]")?.textContent === "Templates are up to date.");
globalThis.__update = { ok: true, added: [{ key: "parking", label: "Smart Parking" }],
                        updated: [{ key: "counting", label: "People Counting" }],
                        media: { installed: ["counting"], files: 2, errors: [] } };
globalThis.__sent.length = 0;
globalThis.__gets = [];
await click(chk());
check("...it runs the manual check", globalThis.__sent.some(([v, u]) => v === "POST" && u === "/api/hub/templates/update"),
      JSON.stringify(globalThis.__sent));
check("...says what came in, preview pictures too",
      result() === "New templates: Smart Parking. Updated: People Counting. Preview pictures updated.", result());
check("...and redraws the cards", globalThis.__gets.includes("/api/hub/templates"), JSON.stringify(globalThis.__gets));
globalThis.__update = { ok: true, added: [], updated: [], media: { installed: [] } };
await click(chk());
check("nothing new: Templates are up to date.", result() === "Templates are up to date.", result());
check("...and it is the ONLY status line: the automatic one gives way to it (no double)",
      !chkHost.querySelector("[data-template-refresh]")
      && (chkHost.textContent.match(/Templates are up to date\./g) || []).length === 1,
      chkHost.textContent);
globalThis.__update = new Error("this box has no update token");
await click(chk());
check("a failed check says why, in words", result() === "Could not check for new templates: this box has no update token", result());
ui.unmount();
chkHost.remove();

// ---- 9. the Hub home ------------------------------------------------------------
const tplGet = globalThis.__get;
let hubCards = [];
let cap = 4;
globalThis.__user = { is_admin: true };
globalThis.__get = (url) => (url === "/api/hub"
  ? { ok: true, cards: hubCards, max_systems: cap, system_count: hubCards.length, groups: [] }
  : tplGet(url));
const sys = (n) => ({ slug: `s${n}`, name: `System ${n}`, url: `http://x/${n}`, status: "ok", summary: "" });
const hubHost = document.createElement("div");
document.body.appendChild(hubHost);
const slotsShown = () => hubHost.querySelectorAll("[data-new-slot]").length;
for (const [n, want] of [[0, 4], [1, 3], [3, 1]]) {
  hubCards = Array.from({ length: n }, (_, i) => sys(i));
  ui = m.mountHub(hubHost);
  // eslint-disable-next-line no-await-in-loop
  await wait(0);
  check(`${n} system(s): ${want} New system tile(s)`, slotsShown() === want, slotsShown());
  if (n === 1) {
    const html = hubHost.innerHTML;
    check("the templates sit above Your systems",
          html.indexOf("data-template-gallery") > -1
          && html.indexOf("data-template-gallery") < html.indexOf("Your systems")
          && html.indexOf("Your systems") < html.indexOf("System 0"));
    await click(button("Select"));
    check("...and stay while selecting", !!hubHost.querySelector("[data-template-gallery]"));
  }
  ui.unmount();
}
hubCards = Array.from({ length: 4 }, (_, i) => sys(i));
ui = m.mountHub(hubHost);
await wait(0);
const limitLine = hubHost.querySelector("[data-template-limit]")?.textContent || "";
check("at the cap: no New system tile", slotsShown() === 0);
check("...the cards stay, with the plain notice",
      cards(hubHost).length === 3
      && limitLine === "You have reached the limit of 4 systems. Remove one to add another.", limitLine);
// The toolbar's New System button is gone (Tok Aba, 2026-10-27): the dashed
// tiles are the only way in, and at the cap there are none.
check("...and there is no New System button in the toolbar",
      !button("New System"));
await click(card("counting", hubHost));
check("...a card still opens its preview, Use this template disabled with the reason",
      button("Use this template")?.disabled === true
      && (document.querySelector("[data-template-preview]")?.textContent || "").includes("limit of 4 systems"));
await click(button("Use this template"));
check("...and pressing it creates nothing", globalThis.__creates.length === 0);
ui.unmount();
hubCards = [sys(0)];
ui = m.mountHub(hubHost);
await wait(0);
await click(hubHost.querySelector("[data-new-slot]"));
await click(card("restricted_zone", hubHost));
await click(button("Use this template"));
check("below the cap: a tile starts from Blank Canvas, a card from its template",
      JSON.stringify(globalThis.__creates) === JSON.stringify([[undefined, undefined], [undefined, "restricted_zone"]]),
      JSON.stringify(globalThis.__creates));
ui.unmount();
cap = 0;
hubCards = Array.from({ length: 5 }, (_, i) => sys(i));
ui = m.mountHub(hubHost);
await wait(0);
check("no cap (developer): one place always free", slotsShown() === 1 && !hubHost.querySelector("[data-template-limit]"));
ui.unmount();
// A system whose name equals its id shows the id once, not under itself; one
// with a different name still shows the id beneath it.
globalThis.__user = { is_admin: true };
cap = 4;
hubCards = [{ ...sys(0), slug: "test", name: "test" }, { ...sys(1), slug: "pcb_sensata", name: "Panel Detection" }];
ui = m.mountHub(hubHost);
await wait(0);
const idLines = [...hubHost.querySelectorAll(".font-mono")].map((e) => e.textContent);
check("a system named like its id shows no id line", !idLines.includes("test"), idLines.join());
check("...and one named differently shows its id under the name", idLines.includes("pcb_sensata"), idLines.join());
ui.unmount();
globalThis.__user = { is_admin: false };
cap = 4;
hubCards = [sys(0)];
ui = m.mountHub(hubHost);
await wait(0);
check("a non-admin: no templates, no tiles, no heading",
      !hubHost.querySelector("[data-template-gallery]") && slotsShown() === 0
      && !hubHost.textContent.includes("Your systems"));
ui.unmount();
hubHost.remove();

console.log(bad ? `FAILED (${bad})` : "PASS");
process.exit(bad ? 1 : 0);
