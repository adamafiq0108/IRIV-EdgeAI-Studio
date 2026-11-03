// "Show video when these classes are detected" ([video] view_classes): the
// checkboxes on the Detection page's Video Visibility block, the real
// VideoSavingPanel.jsx in jsdom with the api client replaced by a recorder.
//
//   1. one checkbox per class of the ACTIVE model, none hard-coded; every box
//      ticked while nothing was chosen (absent = every class)
//   2. unticking one saves the rest; ticking the last one back saves null
//      ("every class", so a later model's classes are covered too)
//   3. unticking everything saves an empty list and says the video stays hidden
//   4. a model with no class list yet says so and offers nothing to tick
//   5. a model switch: a saved choice naming classes the model lacks is pruned
//      quietly, and one with nothing the model can detect goes back to null
//   6. the block only exists while "Show video only when an object is detected" is on
//
//   node tests/check_viewclasses.mjs
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".viewclasses-test.cjs");

const dom = new JSDOM("<!doctype html><html><body></body></html>",
                      { pretendToBeVisual: true, url: "https://localhost/" });
global.window = dom.window;
global.document = dom.window.document;
Object.defineProperty(global, "navigator", { value: dom.window.navigator, configurable: true });
for (const k of ["HTMLElement", "Element", "Node", "SVGElement", "Event", "CustomEvent",
                 "MouseEvent", "requestAnimationFrame", "cancelAnimationFrame",
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

// The api client: GET answers whatever the test put in globalThis.__video, a
// POST is recorded and merged into it the way the server would.
const shimApi = {
  name: "shim-api",
  setup(b) {
    b.onResolve({ filter: /api\/client\.js$/ }, () => ({ path: "api-shim", namespace: "shim" }));
    b.onLoad({ filter: /^api-shim$/, namespace: "shim" }, () => ({
      contents: `
        globalThis.__posts = [];
        export const api = {
          get() { return Promise.resolve({ ok: true, video: { ...globalThis.__video },
                                           needs_client: "needs boxes drawn in the browser" }); },
          post(url, body) {
            globalThis.__posts.push([url, body]);
            const v = { ...globalThis.__video };
            for (const [k, val] of Object.entries(body)) {
              if (val === null) delete v[k]; else v[k] = val;
            }
            globalThis.__video = v;
            return Promise.resolve({ ok: true, video: v });
          },
        };
        export function friendlyError(e) { return String(e && e.message || e); }
      `,
      resolveDir: FRONT, loader: "js",
    }));
  },
};
const p = (rel) => join(FRONT, rel).replace(/\\/g, "/");
await build({
  plugins: [shimApi],
  stdin: {
    contents: `
      import { createRoot } from "react-dom/client";
      import { act, createElement as h } from "react";
      import VideoSavingPanel from "${p("src/components/VideoSavingPanel.jsx")}";
      import { ToastProvider } from "${p("src/components/ui/Toast.jsx")}";
      export { act, h, VideoSavingPanel, ToastProvider };
      export function mount(el, node) {
        const root = createRoot(el);
        act(() => { root.render(node); });
        return { rerender: (n) => act(() => root.render(n)), unmount: () => act(() => root.unmount()) };
      }`,
    resolveDir: FRONT, loader: "js",
  },
  bundle: true, format: "cjs", platform: "node", outfile: OUT, jsx: "automatic", logLevel: "silent",
  define: { "process.env.NODE_ENV": '"development"' },
});
const m = req(OUT);
const { h } = m;
const wait = (ms = 0) => m.act(async () => { await new Promise((ok) => setTimeout(ok, ms)); });

const BASE = { on_demand: false, view_on_detect: true, view_hold_s: 30, overlay: "client" };
const panel = (classes) => h(m.ToastProvider, null,
  h(m.VideoSavingPanel, { editable: true, classes }));
const boxes = (root) => [...root.querySelectorAll('[data-view-classes] input[type="checkbox"]')];
const names = (root) => boxes(root).map((b) => b.parentElement.textContent.trim());
const ticked = (root) => boxes(root).filter((b) => b.checked).map((b) => b.parentElement.textContent.trim());

async function open(video, classes) {
  globalThis.__video = { ...BASE, ...video };
  globalThis.__posts = [];
  const box = document.createElement("div");
  document.body.appendChild(box);
  const r = m.mount(box, panel(classes));
  await wait(5);
  return { box, r, close() { r.unmount(); document.body.removeChild(box); } };
}
async function click(el) {
  await m.act(async () => {
    el.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
    await new Promise((ok) => setTimeout(ok, 5));
  });
}

// -- 1. the model's own classes, all ticked while nothing was chosen -----------------
{
  const t = await open({}, ["cat", "dog", "bird"]);
  check("one checkbox per class of the active model, in its order",
        JSON.stringify(names(t.box)) === '["cat","dog","bird"]', JSON.stringify(names(t.box)));
  check("...all ticked while nothing was chosen", ticked(t.box).length === 3);

  // -- 2. unticking one saves the rest; ticking it back saves null -------------------
  await click(boxes(t.box)[1]);
  check("unticking dog saves the other two",
        JSON.stringify(globalThis.__posts.at(-1)) ===
          JSON.stringify(["/api/detector/control/video", { view_classes: ["cat", "bird"] }]),
        JSON.stringify(globalThis.__posts));
  check("...and the box shows it", JSON.stringify(ticked(t.box)) === '["cat","bird"]', JSON.stringify(ticked(t.box)));
  await click(boxes(t.box)[1]);
  check("ticking dog again saves null: every class",
        JSON.stringify(globalThis.__posts.at(-1)[1]) === '{"view_classes":null}',
        JSON.stringify(globalThis.__posts.at(-1)));

  // -- 3. none ticked ---------------------------------------------------------------
  for (const b of boxes(t.box)) { if (b.checked) await click(b); }
  check("unticking all saves an empty list",
        JSON.stringify(globalThis.__posts.at(-1)[1]) === '{"view_classes":[]}',
        JSON.stringify(globalThis.__posts.at(-1)));
  check("...and says the live video stays hidden",
        !!t.box.querySelector("[data-view-classes-none]"));
  t.close();
}

// -- 4. no class list yet ------------------------------------------------------------
{
  const t = await open({}, []);
  check("a model with no class list: nothing to tick, and a plain message",
        boxes(t.box).length === 0 && !!t.box.querySelector("[data-view-classes-empty]"));
  t.close();
}

// -- 5. a model switch ---------------------------------------------------------------
{
  const t = await open({ view_classes: ["cat", "dog"] }, ["cat", "bird"]);
  await wait(10);
  check("a choice naming a class the model lacks is pruned quietly",
        globalThis.__posts.some(([, b]) => JSON.stringify(b) === '{"view_classes":["cat"]}'),
        JSON.stringify(globalThis.__posts));
  t.close();
  const u = await open({ view_classes: ["cat", "dog"] }, ["car", "bus"]);
  await wait(10);
  check("one with nothing the model can detect goes back to every class",
        globalThis.__posts.some(([, b]) => JSON.stringify(b) === '{"view_classes":null}'),
        JSON.stringify(globalThis.__posts));
  u.close();
  const v = await open({ view_classes: [] }, ["car", "bus"]);
  await wait(10);
  check("an explicit 'none' is not touched by a model switch", globalThis.__posts.length === 0,
        JSON.stringify(globalThis.__posts));
  v.close();
  const w = await open({ view_classes: ["cat"] }, ["cat", "bird"]);
  await wait(10);
  check("a choice the model fully covers is left alone", globalThis.__posts.length === 0);
  w.close();
}

// -- 6. only while the switch is on ---------------------------------------------------
{
  const t = await open({ view_on_detect: false }, ["cat", "dog"]);
  check("no class block while 'show video only when an object is detected' is off",
        !t.box.querySelector("[data-view-classes]"));
  t.close();
}

if (bad) { console.error(`\n${bad} check(s) failed`); process.exit(1); }
console.log("\nall view-class checks passed");
dom.window.close();
process.exit(0);
