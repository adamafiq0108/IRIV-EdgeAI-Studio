// The Monitor account's two panels render and behave: the switch card (System
// settings) with the token alert in front and no sign-in fields, and the access
// page (Accounts > Monitor) with grouped page tiles, the systems choice, an
// unsaved-changes bar, and a Save that posts both grants.
//
//   node tests/check_monitorpanels.mjs
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".monitorpanels-test.cjs");

const dom = new JSDOM("<!doctype html><html><body></body></html>",
                      { pretendToBeVisual: true, url: "http://192.168.1.21:5100/" });
global.window = dom.window;
global.document = dom.window.document;
Object.defineProperty(global, "navigator",
                      { value: dom.window.navigator, configurable: true });
for (const k of ["HTMLElement", "HTMLInputElement", "Element", "Node", "SVGElement",
                 "Event", "CustomEvent", "MouseEvent", "PointerEvent", "KeyboardEvent",
                 "requestAnimationFrame", "cancelAnimationFrame", "getComputedStyle",
                 "localStorage"]) {
  if (dom.window[k]) global[k] = dom.window[k];
}
global.IS_REACT_ACT_ENVIRONMENT = true;
global.sessionStorage = dom.window.sessionStorage;

let bad = 0;
const check = (msg, passed, detail = "") => {
  if (passed) console.log(`ok   ${msg}`);
  else { console.error(`FAIL ${msg}${detail ? `\n      ${detail}` : ""}`); bad += 1; }
};

const shim = (name, filter, contents) => ({
  name,
  setup(b) {
    b.onResolve({ filter }, () => ({ path: name, namespace: "shim" }));
    b.onLoad({ filter: new RegExp(`^${name}$`), namespace: "shim" }, () => ({
      contents, resolveDir: FRONT, loader: "js" }));
  },
});
const shims = [
  shim("api-shim", /api\/client\.js$/, `
    globalThis.__posts = [];
    export const api = {
      get() { return Promise.resolve(globalThis.__get); },
      post(url, body) { globalThis.__posts.push([url, body]);
        return Promise.resolve(globalThis.__get); },
      del(url) { globalThis.__posts.push(["DELETE " + url]); return Promise.resolve(globalThis.__get); },
    };
    export function friendlyError(e) { return String(e && e.message || e); }`),
  shim("ask-shim", /ui\/ask\.jsx$/, `
    export function useConfirm() { return async () => true; }`),
  shim("toast-shim", /ui\/Toast\.jsx$/, `
    globalThis.__toasts = [];
    export function useToast() { return {
      success: (t) => globalThis.__toasts.push(["ok", t]),
      error: (t) => globalThis.__toasts.push(["error", t]) }; }`),
];

const p = (rel) => join(FRONT, rel).replace(/\\/g, "/");
await build({
  plugins: shims,
  stdin: {
    contents: `
      import { createRoot } from "react-dom/client";
      import { act, createElement as h } from "react";
      import { MonitorAccess, MonitorSwitchCard } from "${p("src/components/MonitorPanels.jsx")}";
      export { act };
      export function mount(el, which) {
        const root = createRoot(el);
        act(() => { root.render(h(which === "card" ? MonitorSwitchCard : MonitorAccess)); });
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
async function render(which, state, fn) {
  globalThis.__get = state;
  globalThis.__posts.length = 0;
  const box = document.createElement("div");
  document.body.appendChild(box);
  const r = m.mount(box, which);
  await flush();
  try { return await fn(box); } finally { r.unmount(); document.body.removeChild(box); }
}

const GRANT = ["main", "detection", "workflow", "dataset", "dataset_library", "analytics",
               "footage", "events", "database", "models", "logs"]
  .map((page) => ({ page, label: page }));
const ON = { ok: true, configured: true, enabled: true, username: "monitor",
             pages: ["main", "analytics", "footage"], grantable: GRANT,
             default_pages: ["main", "analytics", "footage"],
             systems: { all: true, slugs: [] }, tokens: [], has_token: false,
             available: [{ slug: "a", name: "Line A" }, { slug: "b", name: "Line B" }],
             box: { hostname: "box", mac: "aa:bb", macs: [{ port: "eth0", mac: "aa:bb" }] } };

// -- the access page -------------------------------------------------------------
await render("access", ON, async (box) => {
  const tiles = [...box.querySelectorAll("[data-tile]")];
  const on = tiles.filter((t) => t.getAttribute("data-tile") === "on")
    .map((t) => t.textContent.trim());
  check("pages are tiles, the three defaults ticked",
        on.some((t) => t.startsWith("main")) && on.some((t) => t.startsWith("analytics"))
        && on.some((t) => t.startsWith("footage")), on.join(","));
  check("...grouped under headings (Overview, AI Configuration ...)",
        /Overview/i.test(box.textContent) && /AI Configuration/i.test(box.textContent));
  check("...the fixed abilities are listed, not ticked",
        /Start and stop detection/.test(box.textContent) && /Download footage/.test(box.textContent)
        && /Cannot change anything else/.test(box.textContent));
  const save = [...box.querySelectorAll("button")].find((b) => /Save access/.test(b.textContent));
  check("nothing changed: 'All changes are saved' and Save is disabled",
        /All changes are saved/.test(box.textContent) && save.disabled);
  const wf = tiles.find((t) => t.textContent.trim().startsWith("workflow"));
  await click(wf);
  check("ticking a page: unsaved changes and Save enabled",
        /unsaved changes/.test(box.textContent)
        && ![...box.querySelectorAll("button")].find((b) => /Save access/.test(b.textContent)).disabled);
  const choose = [...box.querySelectorAll("[data-tile]")].find((t) => /Choose systems/.test(t.textContent));
  await click(choose);
  check("'Choose systems' lists this box's systems as tiles",
        /Line A/.test(box.textContent) && /Line B/.test(box.textContent));
  await click([...box.querySelectorAll("[data-tile]")].find((t) => /Line B/.test(t.textContent)));
  await click([...box.querySelectorAll("button")].find((b) => /Save access/.test(b.textContent)));
  check("Save posts the pages and then the systems",
        globalThis.__posts[0]?.[0] === "/api/hub/monitor/pages"
        && globalThis.__posts[0][1].pages.includes("workflow")
        && globalThis.__posts[1]?.[0] === "/api/hub/monitor/systems"
        && globalThis.__posts[1][1].all === false
        && JSON.stringify(globalThis.__posts[1][1].systems) === JSON.stringify(["b"]),
        JSON.stringify(globalThis.__posts));
  check("...no em dash", !box.textContent.includes("—"));
});

await render("access", { ...ON, enabled: false }, async (box) => {
  check("switched off: it says so and shows no tiles",
        /Monitor account is off/.test(box.textContent) && !box.querySelector("[data-tile]"));
});

// -- the switch card ----------------------------------------------------------------
await render("card", { ...ON, tokens: [{ id: "t1", name: "Plant", created: 1, last_used: 0, last_ip: "" }],
                       has_token: true }, async (box) => {
  check("the switch card has no username or password fields",
        !box.querySelector("input[type=password]") && !/Save sign-in/.test(box.textContent));
  const alert = box.querySelector("[data-token-alert]");
  check("the tokens notice is an alert shown in front, not behind an info icon",
        alert && /Important: Each device has a unique, one-time-visible token/.test(alert.textContent)
        && alert.getAttribute("role") === "alert");
  check("each device is listed with a Remove button and a Create token form",
        /Plant/.test(box.textContent)
        && [...box.querySelectorAll("button")].some((b) => /Remove/.test(b.textContent))
        && [...box.querySelectorAll("button")].some((b) => /Create token/.test(b.textContent)));
  await click([...box.querySelectorAll("button")].find((b) => /Remove/.test(b.textContent)));
  check("Remove deletes that device's token",
        globalThis.__posts.some((x) => /^DELETE \/api\/hub\/monitor\/token\/t1/.test(x[0])),
        JSON.stringify(globalThis.__posts));
});

if (bad) { console.error(`\n${bad} check(s) failed`); process.exit(1); }
console.log("\nall monitor panel checks passed");
process.exit(0);
