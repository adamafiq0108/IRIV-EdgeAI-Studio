// AI Models: a RUNNING built-in model's card carries the Detection settings
// form (classes + confidence), the same one the Custom Models card folds
// under its cards (Tok Aba 2026-10-05). Since the built-ins were listed on
// their own, a system running only yolov8s had no place on this page to set
// either; only the dashboard's "AI model classes" card could.
//
//   1. the running built-in card has the fold; open, it shows Detection
//      settings seeded from the server (confidence, the kept classes)
//   2. a built-in that is NOT running shows neither the fold nor the form
//   3. ticking a class and saving posts model + classes through the same
//      route the dashboard card uses, and keeps the confidence as it was
//
//   node tests/check_builtinsettings.mjs
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".builtinsettings-test.cjs");

const dom = new JSDOM("<!doctype html><html><body></body></html>",
                      { pretendToBeVisual: true, url: "https://localhost/" });
global.window = dom.window;
global.document = dom.window.document;
Object.defineProperty(global, "navigator",
                      { value: dom.window.navigator, configurable: true });
for (const k of ["HTMLElement", "HTMLInputElement", "Element", "Node",
                 "SVGElement", "Image", "Event", "CustomEvent", "MouseEvent",
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

// The api client, under the test's control: GET answers globalThis.__get
// (a value, or a rejection for the routes a box without that feature 400s),
// a POST is recorded and answers what it was sent.
const shimApi = {
  name: "shim-api",
  setup(b) {
    b.onResolve({ filter: /api\/client\.js$/ }, () => (
      { path: "api-shim", namespace: "shim" }));
    b.onLoad({ filter: /^api-shim$/, namespace: "shim" }, () => ({
      contents: `
        globalThis.__posts = [];
        export const api = {
          get(url) { return globalThis.__get(url); },
          post(url, body) { globalThis.__posts.push([url, body]);
                            return Promise.resolve({ ok: true, ...body }); },
          put() { return Promise.resolve({}); },
        };
        export class ApiError extends Error {}
        export function friendlyError(e) { return String(e && e.message || e); }
        export function saveBlob() {}
      `,
      resolveDir: FRONT, loader: "js",
    }));
  },
};

const p = (rel) => join(FRONT, rel).replace(/\\/g, "/");
const ENTRY = `
import { createRoot } from "react-dom/client";
import { act, createElement as h } from "react";
import { MemoryRouter } from "react-router-dom";
import Models from "${p("src/pages/Models.jsx")}";
import { AppContext } from "${p("src/state/app.jsx")}";
import { ToastProvider } from "${p("src/components/ui/Toast.jsx")}";
export { act, h, Models, AppContext, ToastProvider, MemoryRouter };
export function mount(el, node) {
  const root = createRoot(el);
  act(() => { root.render(node); });
  return { unmount: () => act(() => root.unmount()) };
}
`;
await build({
  plugins: [shimApi],
  stdin: { contents: ENTRY, resolveDir: FRONT, loader: "js" },
  bundle: true, format: "cjs", platform: "node", outfile: OUT,
  jsx: "automatic", logLevel: "silent",
  alias: { "@": join(FRONT, "src") },
  loader: { ".css": "empty" },
  define: { "process.env.NODE_ENV": '"development"' },
});
const m = req(OUT);
const { h, act } = m;
const settle = () => act(async () => { await new Promise((r) => setTimeout(r, 30)); });

// A box running yolov8s out of the box: no saved manifest, two built-in HEFs
// on disk, one of them the running model, a one-class filter already saved.
const LABELS = ["person", "car", "dog"];
const ANSWERS = {
  "/api/models": { ok: true, editable: true, models: [],
                   vocab: { archs: ["hailo8l"], detected_arch: "hailo8l" } },
  "/api/detector/control/params": {
    ok: true, editable: true, model: "yolov8s", models_dir: null,
    confidence: 0.45, classes: ["person"], models: [],
    labels: LABELS, labels_by_model: { yolov8s: LABELS } },
  "/api/detector/builtin": { ok: true, editable: true, models: [
    { name: "yolov8s", source: "builtin", downloaded: true, current: true,
      downloaded_archs: ["hailo8l"], description: "small, fast" },
    { name: "yolov8m", source: "builtin", downloaded: true, current: false,
      downloaded_archs: ["hailo8l"], description: "medium" },
  ] },
};
globalThis.__get = (url) => (url in ANSWERS
  ? Promise.resolve(JSON.parse(JSON.stringify(ANSWERS[url])))
  : Promise.reject(new Error(`no ${url} on this box`)));

async function render(fn) {
  globalThis.__posts = [];
  const box = document.createElement("div");
  document.body.appendChild(box);
  const r = m.mount(box, h(m.AppContext.Provider, { value: { nav: [] } },
    h(m.MemoryRouter, { initialEntries: ["/models"] },
      h(m.ToastProvider, null, h(m.Models)))));
  await settle();
  try { return await fn(box); } finally { r.unmount(); document.body.removeChild(box); }
}
const cardOf = (box, name) => [...box.querySelectorAll(".model-card")]
  .find((c) => c.querySelector(".model-card-name")?.textContent === name);
// One act around the click AND the promise it starts (a Save's POST, the
// toast after it), so React never reports a state update outside act.
const click = (el) => act(async () => {
  el.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await new Promise((r) => setTimeout(r, 30));
});

await render(async (box) => {
  const s = cardOf(box, "yolov8s");
  const mcard = cardOf(box, "yolov8m");
  check("both built-in cards are drawn", !!s && !!mcard);
  check("yolov8s is the running one",
        s.textContent.includes("Active")
        && s.textContent.includes("In use · click to stop"), s?.textContent);

  // 1. the running card: fold shut by default, open shows the form seeded
  //    from the server.
  const fold = s.querySelector(".model-card-fold");
  check("the running built-in card has the fold at its foot", !!fold);
  check("...shut by default: no Detection settings until it is opened",
        !s.querySelector("[data-builtin-settings]")
        && !s.textContent.includes("Detection settings"));
  await click(fold);
  await settle();
  const form = s.querySelector("[data-builtin-settings]");
  check("open: Detection settings sit under the running built-in card",
        !!form && form.textContent.includes("Detection settings"),
        s.textContent.slice(0, 300));
  check("...the buttons above it are untouched",
        s.textContent.includes("In use · click to stop"));
  const conf = form?.querySelector('input[type="number"]');
  check("...seeded with the server's confidence",
        conf && Number(conf.value) === 0.45, conf?.outerHTML);
  const ticks = [...(form?.querySelectorAll('input[type="checkbox"]') || [])];
  const tickOf = (name) => ticks.find((t) => t.closest("label")?.textContent.trim() === name);
  check("...and this model's classes, the kept one ticked",
        ticks.length === LABELS.length && tickOf("person")?.checked === true
        && tickOf("car")?.checked === false, String(ticks.length));

  // 2. the built-in that is not running: nothing to set.
  check("a built-in that is not running has no fold and no settings",
        !mcard.querySelector(".model-card-fold")
        && !mcard.querySelector("[data-builtin-settings]")
        && !mcard.textContent.includes("Detection settings"),
        mcard.textContent);

  // 3. tick a class, save: the dashboard card's route, model named, the
  //    confidence kept as it was.
  await click(tickOf("car"));
  await settle();
  const save = [...form.querySelectorAll("button")]
    .find((b) => b.textContent === "Save settings");
  check("a tick marks the form unsaved and frees Save",
        form.textContent.includes("unsaved") && save && !save.disabled);
  await click(save);
  await settle();
  const post = globalThis.__posts.find(([u]) => u === "/api/detector/control/params");
  check("Save posts through /api/detector/control/params",
        !!post, JSON.stringify(globalThis.__posts));
  check("...naming the model, with both classes, confidence unchanged",
        post && post[1].model === "yolov8s"
        && JSON.stringify(post[1].classes) === JSON.stringify(["person", "car"])
        && post[1].confidence === 0.45, JSON.stringify(post && post[1]));
  check("...and no other route was written to",
        globalThis.__posts.every(([u]) => u === "/api/detector/control/params"),
        JSON.stringify(globalThis.__posts));
  check("...the form settles back to saved",
        !form.textContent.includes("unsaved"));
});

if (bad) { console.error(`${bad} check(s) failed`); process.exit(1); }
console.log("all built-in settings checks passed");
dom.window.close();
process.exit(0);
