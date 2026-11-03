// The create wizard brings the templates up to date as it opens.
//
// Tok Aba (2026-10-14): clicking New system fetches the latest published
// templates first, so the new system is made from the newest one; offline is
// fine and silent. Since 2026-10-22 the cards live on the Hub home
// (TemplateGallery.jsx), so the visible line moved there; the wizard still
// asks, silently, so Create waits as before. Pinned by rendering both
// against a shimmed api whose POST /api/hub/templates/refresh the test
// answers by hand:
//   1. the refresh is asked for ONCE, as the cards mount; the cards are
//      there at once with "Checking for the latest templates..."
//   2. a card can be opened (its preview) while it runs
//   3. each answer has its line: updated / up to date / this device (offline,
//      skipped, and a refresh that failed outright)
//   4. "updated" redraws the list (a second GET) and the open preview
//      shows the newest content
//   5. Create is NEVER disabled by the refresh; pressed while it runs, it
//      waits for the answer, re-reads the list if anything landed, then
//      sends the same key
//   6. no em dash
//
//   node tests/check_templateautorefresh.mjs
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".templateautorefresh-test.cjs");

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
  global[k] = dom.window[k];
}
global.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
global.IS_REACT_ACT_ENVIRONMENT = true;

let bad = 0;
const check = (msg, passed, detail = "") => {
  if (passed) console.log(`ok   ${msg}`);
  else { console.error(`FAIL ${msg}${detail ? `\n      ${detail}` : ""}`); bad += 1; }
};

// GETs answer globalThis.__get(url) and are logged; POSTs are logged, the
// refresh answered by globalThis.__refresh() (a promise the test controls).
const shimApi = {
  name: "shim-api",
  setup(b) {
    b.onResolve({ filter: /client\.js$/ }, (a) => (
      a.path.endsWith("api/client.js")
      || (a.path === "./client.js" && /[\\/]src[\\/]api$/.test(a.resolveDir))
        ? { path: "api-shim", namespace: "shim" } : undefined));
    b.onLoad({ filter: /^api-shim$/, namespace: "shim" }, () => ({
      contents: `
        globalThis.__log = [];
        export const api = {
          get(url) { globalThis.__log.push(["GET", url]); return Promise.resolve(globalThis.__get(url)); },
          post(url, body) {
            globalThis.__log.push(["POST", url, body]);
            if (url === "/api/hub/templates/refresh") return globalThis.__refresh();
            return Promise.resolve({ ok: true, name: body.name, started: true, url: "http://x", warnings: [] });
          },
        };
        export function friendlyError(e) { return String(e && e.message || e); }
      `,
      resolveDir: FRONT, loader: "js",
    }));
  },
};
const shimApp = {
  name: "shim-app",
  setup(b) {
    b.onResolve({ filter: /state\/app\.jsx$/ }, () => ({ path: "app-shim", namespace: "shim" }));
    b.onLoad({ filter: /^app-shim$/, namespace: "shim" }, () => ({
      contents: `export function useApp() { return { user: { is_admin: true } }; }`,
      resolveDir: FRONT, loader: "js",
    }));
  },
};

const p = (rel) => join(FRONT, rel).replace(/\\/g, "/");
await build({
  plugins: [shimApi, shimApp],
  stdin: {
    contents: `
      import { createRoot } from "react-dom/client";
      import { act, createElement as h } from "react";
      import CreateSystemWizard from "${p("src/components/CreateSystemWizard.jsx")}";
      import TemplateGallery from "${p("src/components/TemplateGallery.jsx")}";
      export { act };
      export function mountWizard(el, template) {
        const root = createRoot(el);
        act(() => { root.render(h(CreateSystemWizard, { template })); });
        return { unmount: () => act(() => root.unmount()) };
      }
      export function mountGallery(el) {
        const root = createRoot(el);
        act(() => { root.render(h(TemplateGallery, { onUse: () => {} })); });
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
const line = () => document.querySelector("[data-template-refresh]")?.textContent || "";
const card = (label) => [...document.querySelectorAll("[data-template-card]")]
  .find((b) => b.textContent.includes(label));
const preview = () => document.querySelector("[data-template-preview]")?.textContent || "";
const refreshes = () => globalThis.__log.filter(([v, u]) => v === "POST" && u === "/api/hub/templates/refresh");
const listReads = () => globalThis.__log.filter(([v, u]) => v === "GET" && u === "/api/hub/templates");

let peopleLabel = "People counting";
globalThis.__get = (url) => {
  if (url === "/api/hub/port") return { ok: true, running: 5100 };
  if (url.startsWith("/api/hub/ports/check")) return { ok: true, suggested: 5113 };
  if (url === "/api/hub/templates") {
    return { ok: true, saved: [], updates: { token: true },
             templates: [{ key: "people", label: peopleLabel, hint: "" },
                         { key: "blank_canvas", label: "Blank Canvas", hint: "" }] };
  }
  return { ok: true };
};

// A refresh the test answers when it chooses.
let settle = null;
const pending = () => { globalThis.__refresh = () => new Promise((res, rej) => { settle = { res, rej }; }); };

const host = document.createElement("div");
document.body.appendChild(host);

// ---- 1-4: opening, the lines, the redraw ----------------------------------------
pending();
let ui = m.mountGallery(host);
await wait(0);
check("the refresh is asked for once, as the cards mount", refreshes().length === 1,
      JSON.stringify(globalThis.__log));
check("the cards are there at once", !!card("People counting") && !!card("Blank Canvas"));
check("...with the checking line", line() === "Checking for the latest templates...", line());
await click(card("People counting"));
check("a card can be opened while it runs",
      preview().includes("People counting") && !!button("Use this template"));
const readsBefore = listReads().length;
peopleLabel = "People counting v2";
await act(async () => { settle.res({ ok: true, state: "updated", count_changed: 1, changed: ["people"] }); });
await wait(0);
check("updated: says so", line() === "Templates updated. You are choosing from the newest ones.", line());
check("...the list is read again and shows the new template",
      listReads().length === readsBefore + 1 && !!card("People counting v2"),
      JSON.stringify(listReads()));
check("...and the open preview shows it", preview().includes("People counting v2"), preview());
check("still one refresh after the redraw", refreshes().length === 1);
ui.unmount();

for (const [state, want] of [["current", "Templates are up to date."],
                             ["offline", "Using the templates on this device."],
                             ["skipped", "Using the templates on this device."]]) {
  globalThis.__refresh = () => Promise.resolve({ ok: true, state, count_changed: 0 });
  ui = m.mountGallery(host);
  // eslint-disable-next-line no-await-in-loop
  await wait(0);
  check(`${state}: "${want}"`, line() === want, line());
  ui.unmount();
}
globalThis.__refresh = () => Promise.reject(new Error("HTTP 502"));
ui = m.mountGallery(host);
await wait(0);
check("a refresh that failed outright: the templates on this device, no error shown",
      line() === "Using the templates on this device." && !host.textContent.includes("502"), line());
ui.unmount();

// ---- 5: Create while it runs ----------------------------------------------------
peopleLabel = "People counting";
pending();
globalThis.__log.length = 0;
ui = m.mountWizard(host, "people");
await wait(0);
check("the wizard asks for the refresh too, silently",
      refreshes().length === 1 && !host.querySelector("[data-template-refresh]"));
await type(host.querySelector('[aria-label="System name"]'), "Dock");
await wait(0);
check("Next on the name step is not held by the refresh", button("Next")?.disabled === false);
await click(button("Next"));
check("Create is not disabled while the refresh runs",
      button("Create system")?.disabled === false && line() === "",
      button("Create system")?.outerHTML);
await click(button("Create system"));
const creates = () => globalThis.__log.filter(([v, u]) => v === "POST" && u === "/api/hub/systems");
check("...pressed, it waits for the refresh rather than racing it", creates().length === 0,
      JSON.stringify(globalThis.__log));
const readsAt = listReads().length;
peopleLabel = "People counting v3";
await act(async () => { settle.res({ ok: true, state: "updated", count_changed: 1, changed: ["people"] }); });
await wait(0);
const order = globalThis.__log.map(([v, u]) => `${v} ${u}`);
const lastRead = order.lastIndexOf("GET /api/hub/templates");
check("...the list is re-read before the create goes out",
      listReads().length === readsAt + 1 && lastRead < order.indexOf("POST /api/hub/systems"),
      JSON.stringify(order));
check("...and the create sends the same key, once",
      creates().length === 1 && creates()[0][2].template === "people",
      JSON.stringify(creates()));
ui.unmount();

// A refresh that never answers well still lets Create through.
globalThis.__refresh = () => Promise.reject(new Error("network"));
globalThis.__log.length = 0;
ui = m.mountWizard(host, "blank_canvas");
await wait(0);
await type(host.querySelector('[aria-label="System name"]'), "Yard");
await wait(0);
await click(button("Next"));
await click(button("Create system"));
check("a failed refresh never blocks Create", creates().length === 1
      && creates()[0][2].template === "blank_canvas", JSON.stringify(globalThis.__log));
check("no em dash in the wizard", !host.textContent.includes(String.fromCharCode(0x2014)));
ui.unmount();
host.remove();

console.log(bad ? `FAILED (${bad})` : "PASS");
process.exit(bad ? 1 : 0);
