// The Dashboard header's detection control: ONE labelled button.
//
// Tok Aba (2026-10-07): the three icon-only buttons (play, restart arrow,
// square stop) beside "Edit dashboard" became one button that says what it
// will do. Pinned, by rendering pages/Main.jsx against a shimmed api:
//   1. stopped: exactly one power button, "Start", accent style, play icon,
//      title/aria "Start detection"; no Restart and no icon-only button left
//   2. Start posts {action:"start"} to /api/detector/control/power, reads
//      "Starting..." disabled while in flight
//   3. the live poll says running: the same slot reads "Stop", neutral style
//      (not accent), square icon
//   4. Stop posts {action:"stop"} to the same route, reads "Stopping..."
//      disabled while in flight, then flips to Start without waiting for the
//      poll (the press is believed for the grace window)
//   5. stalled (alive, mid-load) offers Stop, never Start
//   6. an account the payload says lacks detection.power sees it disabled,
//      with the server's own denial text as the title
//   7. no camera/model set up: no power button at all
//   8. no em dash in the page source
//   9. detection stopped by the box for a missing camera: Stop stays, with a
//      "waiting for camera" note; a Start answered "waiting" toasts that
//
//   node tests/check_dashpower.mjs
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRONT = join(ROOT, "frontend");
const req = createRequire(pathToFileURL(join(FRONT, "package.json")).href);
const { build } = req("esbuild");
const { JSDOM } = req("jsdom");
const OUT = join(FRONT, "node_modules", ".dashpower-test.cjs");

const dom = new JSDOM("<!doctype html><html><body></body></html>",
                      { pretendToBeVisual: true, url: "http://192.168.1.21:5110/" });
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

let bad = 0;
const check = (msg, passed, detail = "") => {
  if (passed) console.log(`ok   ${msg}`);
  else { console.error(`FAIL ${msg}${detail ? `\n      ${detail}` : ""}`); bad += 1; }
};

// GET /api/dashboard answers globalThis.__dash; every POST is recorded and
// stays pending until the test calls globalThis.__release().
const stub = (name, filter, contents) => ({
  name,
  setup(b) {
    b.onResolve({ filter }, () => ({ path: name, namespace: "shim" }));
    b.onLoad({ filter: new RegExp(`^${name}$`), namespace: "shim" },
             () => ({ contents, resolveDir: FRONT, loader: "jsx" }));
  },
});
const shims = [
  // hooks.js imports it as "./client.js", the page as "../api/client.js".
  stub("api-shim", /(^|\/)client\.js$/, `
    globalThis.__posts = [];
    globalThis.__pending = [];
    globalThis.__reply = { ok: true };
    globalThis.__release = () => { const p = globalThis.__pending.splice(0); p.forEach((r) => r(globalThis.__reply)); };
    export const api = {
      post(url, body) { globalThis.__posts.push([url, body]);
        return new Promise((r) => globalThis.__pending.push(r)); },
      get(url) { return Promise.resolve(JSON.parse(JSON.stringify(globalThis.__dash))); },
    };
    export function friendlyError(e) { return String(e && e.message || e); }
  `),
  stub("app-shim", /state\/app\.jsx$/, `export function useApp() { return { brand: {} }; }`),
  stub("toast-shim", /ui\/Toast\.jsx$/, `
    globalThis.__toasts = [];
    const t = { success(m, d) { globalThis.__toasts.push(["success", m, d]); },
                error(m, d) { globalThis.__toasts.push(["error", m, d]); },
                info(m, d) { globalThis.__toasts.push(["info", m, d]); } };
    export function useToast() { return t; }
  `),
  stub("grid-shim", /widgets\/WidgetGrid\.jsx$/, `export default function WidgetGrid() { return null; }`),
  stub("next-shim", /NextPageButton\.jsx$/, `export default function NextPageButton() { return null; }`),
];

const p = (rel) => join(FRONT, rel).replace(/\\/g, "/");
await build({
  plugins: shims,
  stdin: {
    contents: `
      import { createRoot } from "react-dom/client";
      import { act, createElement as h } from "react";
      import { MemoryRouter } from "react-router-dom";
      import Main from "${p("src/pages/Main.jsx")}";
      export { act };
      export function mount(el) {
        const root = createRoot(el);
        act(() => { root.render(h(MemoryRouter, null, h(Main))); });
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

const flush = async (ms = 0) => {
  await act(async () => { await new Promise((r) => setTimeout(r, ms)); });
  for (let i = 0; i < 4; i += 1) {
    // eslint-disable-next-line no-await-in-loop
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};
const click = async (el) => {
  await act(async () => { el.dispatchEvent(new window.MouseEvent("click", { bubbles: true })); });
  await flush();
};
const dash = (over = {}) => ({
  ok: true, running: false, stalled: false, widgets: [], editable: true,
  setup: { camera: true, model: true }, ...over,
});
const powerButtons = (el) => [...el.querySelectorAll(".page-actions button")];

async function render(payload) {
  globalThis.__dash = payload;
  globalThis.__posts.length = 0;
  globalThis.__pending.length = 0;
  const el = document.createElement("div");
  document.body.appendChild(el);
  const h = m.mount(el);
  await flush();
  return { el, done: () => { h.unmount(); el.remove(); } };
}

// 1-4: one button, Start then Stop, same route.
{
  const { el, done } = await render(dash());
  let btns = powerButtons(el);
  const b = btns[0];
  check("stopped: exactly one power button in the header", btns.length === 1,
        btns.map((x) => x.outerHTML).join("\n"));
  check("stopped: it reads Start, accent style, play icon",
        b && b.textContent.trim() === "Start" && b.classList.contains("btn-accent")
          && !!b.querySelector("svg.lucide-play"), b?.outerHTML);
  check("stopped: title and aria-label say Start detection",
        b?.title === "Start detection" && b?.getAttribute("aria-label") === "Start detection");
  check("no Restart control and no icon-only button left",
        !el.querySelector("[title*='Restart'], svg.lucide-rotate-cw")
          && btns.every((x) => x.textContent.trim().length > 0));

  await click(b);
  check("Start posts {action:start} to /api/detector/control/power",
        JSON.stringify(globalThis.__posts) === JSON.stringify([["/api/detector/control/power", { action: "start" }]]),
        JSON.stringify(globalThis.__posts));
  btns = powerButtons(el);
  check("in flight: one button reading Starting..., disabled",
        btns.length === 1 && btns[0].textContent.trim() === "Starting…" && btns[0].disabled,
        btns[0]?.outerHTML);

  globalThis.__dash = dash({ running: true });
  await act(async () => { globalThis.__release(); });
  await flush(700);
  btns = powerButtons(el);
  const s = btns[0];
  check("running (live poll): the same slot reads Stop, neutral style, square icon",
        btns.length === 1 && s.textContent.trim() === "Stop" && !s.classList.contains("btn-accent")
          && !!s.querySelector("svg.lucide-square") && !s.disabled, s?.outerHTML);
  check("running: title and aria-label say Stop detection",
        s?.title === "Stop detection" && s?.getAttribute("aria-label") === "Stop detection");

  globalThis.__posts.length = 0;
  await click(s);
  check("Stop posts {action:stop} to /api/detector/control/power",
        JSON.stringify(globalThis.__posts) === JSON.stringify([["/api/detector/control/power", { action: "stop" }]]),
        JSON.stringify(globalThis.__posts));
  btns = powerButtons(el);
  check("in flight: one button reading Stopping..., disabled",
        btns.length === 1 && btns[0].textContent.trim() === "Stopping…" && btns[0].disabled,
        btns[0]?.outerHTML);
  // The poll still says running (the old app is winding down): the press wins.
  await act(async () => { globalThis.__release(); });
  await flush();
  btns = powerButtons(el);
  check("after Stop: back to Start at once, even while the poll still says running",
        btns.length === 1 && btns[0].textContent.trim() === "Start" && !btns[0].disabled,
        btns[0]?.outerHTML);
  done();
}

// 5: stalled offers Stop, never Start.
{
  const { el, done } = await render(dash({ running: true, stalled: true }));
  const btns = powerButtons(el);
  check("stalled: Stop is offered, never Start",
        btns.length === 1 && btns[0].textContent.trim() === "Stop" && !btns[0].disabled,
        btns[0]?.outerHTML);
  done();
}

// 6: an account without detection.power.
{
  const { el, done } = await render(dash({ caps: ["main.edit"] }));
  const b = powerButtons(el)[0];
  check("no detection.power: the button is disabled with the server's reason",
        b && b.disabled && /is not allowed on this account/.test(b.title), b?.outerHTML);
  done();
}
{
  const { el, done } = await render(dash({ caps: ["detection.power"] }));
  const b = powerButtons(el)[0];
  check("with detection.power: enabled", b && !b.disabled, b?.outerHTML);
  done();
}

// 7: not set up -- nothing to start.
{
  const { el, done } = await render(dash({ setup: { camera: false, model: true } }));
  check("no camera yet: no power button", powerButtons(el).length === 0);
  done();
}

// 9: stopped by the box for a missing camera (app/camwatch.py): the
// user's switch is on, so Stop stays offered, beside a plain note that it
// starts again by itself; a Start the master answers "waiting" says so.
{
  const { el, done } = await render(dash({ running: true, stalled: true,
                                           ai_paused: "camera_missing" }));
  const btns = powerButtons(el);
  const note = el.querySelector("[data-waiting='camera']");
  check("camera missing: Stop is offered (Stop keeps it stopped)",
        btns.length === 1 && btns[0].textContent.trim() === "Stop" && !btns[0].disabled,
        btns[0]?.outerHTML);
  check("camera missing: a 'waiting for camera' note says it starts again by itself",
        note && note.textContent.trim() === "waiting for camera"
          && note.title === "Detection stopped: camera not found. It starts again "
                            + "by itself when the camera is plugged back in.",
        note?.outerHTML);
  check("camera missing: not the 'detection busy' note",
        !/detection busy/.test(el.textContent), el.textContent);
  done();
}
{
  const { el, done } = await render(dash());
  globalThis.__toasts.length = 0;
  globalThis.__reply = { ok: true, waiting: true,
                         message: "Detection is waiting for the camera: cam1 is not found." };
  await click(powerButtons(el)[0]);
  await act(async () => { globalThis.__release(); });
  await flush();
  check("Start with no camera: the toast says it is waiting, not 'started'",
        globalThis.__toasts.length === 1 && globalThis.__toasts[0][0] === "info"
          && globalThis.__toasts[0][1] === "Waiting for the camera",
        JSON.stringify(globalThis.__toasts));
  globalThis.__reply = { ok: true };
  done();
}

// 8: no em dash.
const src = readFileSync(join(FRONT, "src", "pages", "Main.jsx"), "utf8");
check("no em dash in Main.jsx", !src.includes(String.fromCharCode(0x2014)));

console.log();
if (bad) { console.error(`${bad} check(s) failed`); process.exit(1); }
console.log("all good");
process.exit(0);
